"""持久化文件处理流程：领取租约、在事务外处理，再通过事务原子发布。"""

import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import delete, or_, select, text, update
from sqlalchemy.exc import SQLAlchemyError

from .config import Settings, get_settings
from .db import Database
from .errors import DomainError, require
from .ingestion import PageText, ParsedDocument, chunk_pages, parse_document
from .models import Chunk, Document, DocumentVersion, Job, KnowledgeBase, Upload, User, uid
from .retrieval import Embedder, tokenize

logger = logging.getLogger(__name__)
MAX_EMBEDDING_ATTEMPTS = 3
CLEANUP_INTERVAL_SECONDS = 60


@dataclass(frozen=True)
class JobClaim:
    job_id: str
    version_id: str
    document_id: str
    user_id: str
    token: str
    path: str
    filename: str
    content_hash: str
    cancelled: bool
    provided_text: str | None = None


class Worker:
    def __init__(self, database: Database, settings: Settings):
        self.db, self.settings = database, settings
        self.embedder = Embedder(settings)

    def _claim_next(self) -> JobClaim | None:
        now, token = time.time(), uid()
        with self.db.sessions() as session:
            job = session.scalar(
                select(Job)
                .where(
                    or_(
                        (Job.status == "queued") & (Job.lease_until <= now),
                        (Job.status == "processing") & (Job.lease_until < now),
                    )
                )
                .order_by(Job.created_at)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if job is None:
                return None
            # PostgreSQL 会跳过其他工作进程已锁定的行；这里再通过条件更新
            # 防止 SQLite 下的竞争，因为 SQLite 不执行 FOR UPDATE 行锁。
            changed = session.execute(
                update(Job)
                .where(
                    Job.id == job.id,
                    Job.claim == job.claim,
                    Job.lease_until <= now,
                )
                .values(
                    status="processing",
                    claim=token,
                    lease_until=now + self.settings.job_lease_seconds,
                    attempts=Job.attempts + 1,
                )
            )
            if not changed.rowcount:
                return None
            version = session.get(DocumentVersion, job.version_id)
            document = session.get(Document, job.document_id)
            knowledge_base = session.get(KnowledgeBase, document.kb_id)
            cancelled = bool(
                document.deleted_at or knowledge_base.deleted or document.latest_version != version.id
            )
            if cancelled:
                job.status = version.status = "cancelled"
            claim = JobClaim(
                job.id,
                version.id,
                document.id,
                document.user_id,
                token,
                version.path,
                version.filename,
                version.content_hash,
                cancelled,
                version.provided_text,
            )
            session.commit()
            return claim

    def _stage(self, claim: JobClaim, stage: str) -> None:
        with self.db.sessions() as session:
            job = session.scalar(select(Job).where(Job.id == claim.job_id).with_for_update())
            require(job and job.claim == claim.token, "lease_lost", "任务已由其他进程接管", 409)
            session.get(DocumentVersion, claim.version_id).status = stage
            job.lease_until = time.time() + self.settings.job_lease_seconds
            session.commit()

    def _heartbeat(self, claim: JobClaim, stop: threading.Event) -> None:
        while not stop.wait(max(1, self.settings.job_lease_seconds / 3)):
            try:
                with self.db.sessions() as session:
                    session.execute(
                        update(Job)
                        .where(Job.id == claim.job_id, Job.claim == claim.token, Job.status == "processing")
                        .values(lease_until=time.time() + self.settings.job_lease_seconds)
                    )
                    session.commit()
            except SQLAlchemyError:
                # 发布阶段会再次检查领取标识；租约失效的旧进程绝不能
                # 覆盖接管任务的新进程所产生的结果。
                logger.warning("Could not renew ingestion lease: job=%s", claim.job_id)

    def _reuse_vectors(self, claim: JobClaim, pieces: list[PageText]) -> list[list[float]] | None:
        with self.db.sessions() as session:
            previous = session.scalar(
                select(DocumentVersion)
                .join(Document, Document.id == DocumentVersion.document_id)
                .where(
                    Document.user_id == claim.user_id,
                    Document.deleted_at.is_(None),
                    DocumentVersion.content_hash == claim.content_hash,
                    DocumentVersion.status == "ready",
                    DocumentVersion.embedding_profile == self.embedder.profile,
                    DocumentVersion.id != claim.version_id,
                )
                .limit(1)
            )
            if previous is None:
                return None
            saved = list(
                session.scalars(select(Chunk).where(Chunk.version_id == previous.id).order_by(Chunk.ordinal))
            )
            # 解析或分块规则变化后，仅源文件字节相同不足以复用向量。
            # 必须同时检查处理配置和实际文本块，且不能跨用户共享缓存。
            if [chunk.text for chunk in saved] == [value for _, value in pieces]:
                return [list(chunk.embedding) for chunk in saved]
        return None

    def _publish(
        self, claim: JobClaim, parsed: ParsedDocument, pieces: list[PageText], vectors: list[list[float]]
    ) -> None:
        require(len(pieces) == len(vectors), "embedding_response", "向量服务返回数量异常", 503)
        with self.db.sessions() as session:
            # 保持与 Service.run 一致的“用户 → 文件 → 任务”加锁顺序。
            # 改变顺序可能造成接口写入死锁，或让过期修订覆盖发布结果。
            session.scalar(select(User).where(User.id == claim.user_id).with_for_update())
            document = session.scalar(
                select(Document).where(Document.id == claim.document_id).with_for_update()
            )
            job = session.scalar(select(Job).where(Job.id == claim.job_id).with_for_update())
            version = session.get(DocumentVersion, claim.version_id)
            knowledge_base = session.get(KnowledgeBase, document.kb_id)
            if (
                document.deleted_at
                or knowledge_base.deleted
                or document.latest_version != version.id
                or job.claim != claim.token
            ):
                if job.claim == claim.token:
                    job.status = version.status = "cancelled"
                session.commit()
                return

            session.execute(delete(Chunk).where(Chunk.version_id == version.id))
            for ordinal, ((page, content), vector) in enumerate(zip(pieces, vectors, strict=True)):
                session.add(
                    Chunk(
                        document_id=document.id,
                        version_id=version.id,
                        ordinal=ordinal,
                        page=page,
                        text=content,
                        tokens=" ".join(tokenize(content)),
                        embedding=vector,
                    )
                )
            session.flush()
            if session.bind.dialect.name == "postgresql":
                session.execute(
                    text(
                        "UPDATE chunks SET search_vector = to_tsvector('simple', tokens) "
                        "WHERE version_id = :version"
                    ),
                    {"version": version.id},
                )
            version.text, version.page_count, version.chunk_count = (
                parsed.text,
                parsed.page_count,
                len(pieces),
            )
            # 没有识别出文字的图片只保存原文件，不伪造向量或索引完成时间。
            version.status, version.error = ("ready" if pieces else "stored"), ""
            # 记录实际生成向量的配置，不能沿用任务排队时的旧模型信息。
            version.embedding_profile = self.embedder.profile
            version.indexed_at = time.time() if pieces else None
            document.current_version = version.id
            document.revision += 1  # 新版本发布后，编辑方必须重新读取最新修订号。
            job.status, job.error, job.lease_until = version.status, "", 0
            # 两类索引、提取文本和当前版本指针在同一事务中一起生效。
            session.commit()

    def _record_failure(self, claim: JobClaim, error: Exception) -> None:
        message = error.message if isinstance(error, DomainError) else "文件解析或处理失败，请检查格式后重试"
        # SQLAlchemy 或供应商的原始异常文本可能包含正文或凭证。
        # 日志只记录任务 ID 和异常类型，数据库只保存可安全展示的提示。
        logger.warning("Ingestion failed: job=%s kind=%s", claim.job_id, type(error).__name__)
        with self.db.sessions() as session:
            job = session.scalar(select(Job).where(Job.id == claim.job_id).with_for_update())
            if job is None or job.claim != claim.token:
                return
            version = session.get(DocumentVersion, claim.version_id)
            transient = isinstance(error, DomainError) and error.code == "embedding_unavailable"
            retry = transient and job.attempts < MAX_EMBEDDING_ATTEMPTS
            job.status = version.status = "queued" if retry else "failed"
            job.error = version.error = message
            job.lease_until = time.time() + 10 * job.attempts if retry else 0
            session.commit()

    def tick(self) -> bool:
        """最多处理一个任务；取消已失效任务也视为处理过任务，返回 True。"""
        claim = self._claim_next()
        if claim is None:
            return False
        if claim.cancelled:
            return True
        stop = threading.Event()
        heartbeat = threading.Thread(target=self._heartbeat, args=(claim, stop), daemon=True)
        heartbeat.start()
        try:
            # 解析和外部服务请求均在发布事务之外执行，避免长时间持锁。
            self._stage(claim, "parsing")
            # Agent 已提供图片正文时无需再次 OCR；按版本持久化，重启/重试仍使用同一正文。
            parsed = (
                ParsedDocument([(None, claim.provided_text)], page_count=0)
                if claim.provided_text is not None
                else parse_document(claim.path, claim.filename)
            )
            self._stage(claim, "chunking")
            pieces = chunk_pages(parsed.pages)
            self._stage(claim, "indexing")
            vectors = self._reuse_vectors(claim, pieces)
            if not pieces:
                vectors = []
            elif vectors is None:
                vectors = self.embedder.embed([content for _, content in pieces])
            self._publish(claim, parsed, pieces, vectors)
        except Exception as error:
            # 此处是任务故障隔离边界：单个 PDF 或供应商响应异常
            # 不能终止常驻工作进程，也不能阻塞队列中的其他任务。
            self._record_failure(claim, error)
        finally:
            stop.set()
            heartbeat.join(timeout=2)
        return True

    def cleanup(self) -> None:
        with self.db.sessions() as session:
            for upload in session.scalars(
                select(Upload).where(Upload.expires_at < time.time(), Upload.consumed.is_(False))
            ):
                Path(upload.path).unlink(missing_ok=True)
                session.delete(upload)
            deleted_ids = select(Document.id).where(Document.deleted_at.is_not(None))
            for version in session.scalars(
                select(DocumentVersion).where(DocumentVersion.document_id.in_(deleted_ids))
            ):
                if version.path:
                    Path(version.path).unlink(missing_ok=True)
                    version.path, version.text, version.size = "", "", 0
                    version.provided_text = None
            session.execute(delete(Chunk).where(Chunk.document_id.in_(deleted_ids)))
            session.execute(
                update(Job)
                .where(Job.document_id.in_(deleted_ids), Job.status.in_(["queued", "processing"]))
                .values(status="cancelled", claim=uid(), lease_until=0)
            )
            session.commit()

    def run(self, stop: threading.Event) -> None:
        """独立工作进程与本地开发启动器共用的常驻处理循环。"""
        last_cleanup = 0.0
        while not stop.is_set():
            try:
                if time.time() - last_cleanup > CLEANUP_INTERVAL_SECONDS:
                    self.cleanup()
                    last_cleanup = time.time()
                if not self.tick():
                    stop.wait(1)
            except (SQLAlchemyError, OSError) as error:
                # 临时数据库或文件系统故障不应导致队列永久停止处理。
                logger.warning("Worker temporarily unavailable: kind=%s", type(error).__name__)
                stop.wait(3)


def main() -> None:
    settings = get_settings()
    settings.validate_deployment()
    worker = Worker(Database(settings.database_url), settings)
    stop = threading.Event()
    print("RAG Center worker started; Ctrl+C to stop", flush=True)
    try:
        worker.run(stop)
    except KeyboardInterrupt:
        stop.set()


if __name__ == "__main__":
    main()
