"""文件与文档版本操作。"""

from __future__ import annotations

import base64
import binascii
import hashlib
import time
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import schemas
from ..auth import Principal
from ..errors import DomainError, require
from ..media import is_image
from ..models import ApiKey, Document, DocumentVersion, Job, Upload, uid
from .resources import Operations
from .serializers import document_json, index_details


class DocumentOperations(Operations):
    """文件领域操作，业务事务由 Service.run 统一提交。"""

    def _source(self, session, principal, args, doc=None):
        """在调用方事务中消费绑定目标的上传对象，或校验直接传入的文本。"""
        name = args.name if args.name else doc.name
        if args.upload_id:
            upload = session.get(Upload, args.upload_id)
            require(
                upload
                and upload.user_id == principal.user_id
                and upload.actor_id == principal.actor_id
                and upload.kb_id == (doc.kb_id if doc else args.knowledge_base_id)
                and upload.document_id == (doc.id if doc else None)
                and not upload.consumed
                and upload.received
                and upload.expires_at > time.time(),
                "invalid_upload",
                "上传对象无效、未完成、已消费或目标不匹配",
            )
            body = Path(upload.path).read_bytes()
            self.storage.validate_content(upload.filename, body)
            require(len(body) == upload.size, "invalid_upload", "上传内容已变化")
            upload.consumed = True
            session.flush()
            return upload.filename, body, upload.path
        require(
            Path(name).suffix.lower() in {".txt", ".md"},
            "invalid_input",
            "直接传入文本仅支持 TXT 和 Markdown",
        )
        self.storage.validate_filename(name)
        body = args.text.encode("utf-8")
        self.storage.validate_content(name, body)
        return name, body, None

    def _version(self, session, principal, doc, filename, body, path, provided_text=None):
        """将新版本加入处理队列，保留当前可搜索版本。

        文件系统写入无法随 SQL 事务回滚；写入后失败可能留下未被引用的源文件。
        回滚时不能直接删除已有上传对象，避免误删仍被其他版本引用的文件。
        """
        content_hash = hashlib.sha256(body).hexdigest()
        if provided_text is not None:
            # 同一图片配不同文字必须使用不同缓存键，避免复用其他说明的向量。
            content_hash = hashlib.sha256(
                b"image-text-v1\0" + bytes.fromhex(content_hash) + provided_text.encode("utf-8")
            ).hexdigest()
        require(
            not doc.latest_version
            or session.get(DocumentVersion, doc.latest_version).content_hash != content_hash
            or session.get(DocumentVersion, doc.latest_version).embedding_profile != self.embedder.profile,
            "unchanged_content",
            "内容未发生变化，无需重新建立索引",
            409,
        )
        size = len(body) + (len(provided_text.encode("utf-8")) if provided_text is not None else 0)
        self.quota.check(session, principal, size)
        if path is None:
            path = str((self.settings.storage_dir / f"{uid()}.source").resolve())
            Path(path).write_bytes(body)
        version = DocumentVersion(
            document_id=doc.id,
            filename=filename,
            path=path,
            size=size,
            content_hash=content_hash,
            provided_text=provided_text,
            embedding_profile=self.embedder.profile,
        )
        session.add(version)
        session.flush()
        doc.latest_version = version.id
        # 只有 Worker._publish 可以推进 current_version，保证完整索引发布后再切换。
        job = Job(document_id=doc.id, version_id=version.id)
        session.add(job)
        session.flush()
        return {
            "id": doc.id,
            "document_id": doc.id,
            "version_id": version.id,
            "revision": doc.revision,
            "job_id": job.id,
            "status": "queued",
        }

    def create_document(
        self, session: Session, principal: Principal, key: ApiKey | None, args: schemas.DocCreate
    ) -> dict:
        filename, body, path = self._source(session, principal, args)
        return self._create_document(
            session, principal, args.name, args.knowledge_base_id, filename, body, path
        )

    def _create_document(self, session, principal, name, kb_id, filename, body, path, provided_text=None):
        self.storage.validate_filename(name)
        self.quota.check(session, principal, 0, new_document=True)
        require(
            Path(name).suffix.lower() == Path(filename).suffix.lower(),
            "invalid_filename",
            "显示名称的扩展名必须与原文件类型一致",
        )
        doc = Document(user_id=principal.user_id, kb_id=kb_id, name=name)
        session.add(doc)
        session.flush()
        return self._version(session, principal, doc, filename, body, path, provided_text)

    def create_image_document(
        self, session: Session, principal: Principal, key: ApiKey | None, args: schemas.ImageDocCreate
    ) -> dict:
        self.storage.validate_filename(args.name)
        require(is_image(args.name), "unsupported_type", "此接口仅支持图片和配套文字")
        require(args.text.strip(), "empty_document", "图片配套文字不能为空或仅含空白")
        require("\x00" not in args.text, "invalid_input", "图片配套文字不能包含空字符")
        if args.upload_id:
            filename, body, path = self._source(session, principal, args)
            require(is_image(filename), "unsupported_type", "上传对象必须是图片")
        else:
            # 在解码前按服务器文件上限限制输入，避免 Base64 放大带来的无界分配。
            require(
                len(args.image_base64) <= 4 * ((self.settings.max_file_mb * 1024**2 + 2) // 3),
                "file_too_large",
                "图片超过文件大小限制",
                413,
            )
            try:
                body = base64.b64decode(args.image_base64, validate=True)
            except (binascii.Error, ValueError):
                raise DomainError(
                    "invalid_image_base64", "图片须为标准 Base64 编码，不含 data: 前缀"
                ) from None
            self.storage.validate_content(args.name, body)
            filename, path = args.name, None
        return self._create_document(
            session, principal, args.name, args.knowledge_base_id, filename, body, path, args.text
        )

    def list_documents(
        self, session: Session, principal: Principal, key: ApiKey | None, args: schemas.DocList
    ) -> dict:
        query = select(Document).where(
            Document.kb_id == args.knowledge_base_id, Document.deleted_at.is_(None)
        )
        if args.name:
            query = query.where(Document.name.contains(args.name, autoescape=True))
        if args.status:
            query = query.join(DocumentVersion, Document.latest_version == DocumentVersion.id).where(
                DocumentVersion.status == args.status
            )
        total = session.scalar(select(func.count()).select_from(query.subquery()))
        docs = session.scalars(
            query.order_by(Document.created_at.desc()).offset(args.offset).limit(args.limit)
        )
        return {"items": [document_json(session, doc, self.embedder.profile) for doc in docs], "total": total}

    def get_document(
        self, session: Session, principal: Principal, key: ApiKey | None, args: schemas.DocRef
    ) -> dict:
        return document_json(
            session, self.access.document(session, principal, key, args.document_id), self.embedder.profile
        )

    def read_document(
        self, session: Session, principal: Principal, key: ApiKey | None, args: schemas.DocRead
    ) -> dict:
        doc = self.access.document(session, principal, key, args.document_id)
        require(doc.current_version, "not_ready", "文件尚无可用的文本版本", 409)
        version = session.get(DocumentVersion, doc.current_version)
        end = min(args.offset + args.limit, len(version.text))
        return {
            "document_id": doc.id,
            "version_id": version.id,
            "name": doc.name,
            "text": version.text[args.offset : end],
            "offset": args.offset,
            "next_offset": end if end < len(version.text) else None,
            "total_characters": len(version.text),
            "source": "provided"
            if version.provided_text is not None
            else "ocr"
            if is_image(version.filename)
            else "text",
        }

    def update_document(
        self, session: Session, principal: Principal, key: ApiKey | None, args: schemas.DocUpdate
    ) -> dict:
        doc = self.access.document(session, principal, key, args.document_id)
        require(doc.revision == args.expected_revision, "revision_conflict", "文件已被修改，请重新读取", 409)
        if args.name is not None:
            self.storage.validate_filename(args.name)
            doc.name = args.name
        doc.revision += 1
        if args.text is not None or args.upload_id is not None:
            filename, body, path = self._source(session, principal, args, doc)
            if args.upload_id and args.name is None:
                doc.name = str(Path(doc.name).with_suffix(Path(filename).suffix))
            require(
                Path(doc.name).suffix.lower() == Path(filename).suffix.lower(),
                "invalid_filename",
                "显示名称的扩展名必须与原文件类型一致",
            )
            return self._version(session, principal, doc, filename, body, path)
        original = session.get(DocumentVersion, doc.latest_version)
        require(
            not original or Path(doc.name).suffix.lower() == Path(original.filename).suffix.lower(),
            "invalid_filename",
            "仅改名不能改变文件类型，请上传新文件替换内容",
        )
        return document_json(session, doc, self.embedder.profile)

    def delete_document(
        self, session: Session, principal: Principal, key: ApiKey | None, args: schemas.DocDelete
    ) -> dict:
        doc = self.access.document(session, principal, key, args.document_id)
        require(doc.revision == args.expected_revision, "revision_conflict", "文件已被修改，请重新读取", 409)
        doc.deleted_at = time.time()
        doc.revision += 1
        return {"id": doc.id, "deleted": True, "cleanup": "pending"}

    def get_ingestion_job(
        self, session: Session, principal: Principal, key: ApiKey | None, args: schemas.JobRef
    ) -> dict:
        job = session.get(Job, args.job_id)
        version = session.get(DocumentVersion, job.version_id)
        return {
            "id": job.id,
            "document_id": job.document_id,
            "version_id": job.version_id,
            "status": version.status,
            "attempts": job.attempts,
            "error": job.error,
            **index_details(version, self.embedder.profile),
        }

    def retry_document_processing(
        self, session: Session, principal: Principal, key: ApiKey | None, args: schemas.DocRef
    ) -> dict:
        doc = self.access.document(session, principal, key, args.document_id)
        version = session.get(DocumentVersion, doc.latest_version)
        require(version is not None, "invalid_state", "文件没有可处理的版本", 409)
        require(
            version.status != "stored",
            "no_searchable_text",
            "图片已保存但未识别出文字，请上传更清晰的图片或替换内容",
            409,
        )
        if version.status == "ready":
            require(
                index_details(version, self.embedder.profile)["needs_reindex"],
                "invalid_state",
                "当前真实向量索引已就绪，无需重建",
                409,
            )
            self.embedder.require_configured()
            # 旧版本继续供关键词检索，新模型索引成功发布后再切换。
            doc.revision += 1
            return self._version(
                session,
                principal,
                doc,
                version.filename,
                Path(version.path).read_bytes(),
                version.path,
                version.provided_text,
            )
        require(version.status == "failed", "invalid_state", "仅能重试失败或需要重建的版本", 409)
        if self.settings.embedding_provider == "openai-compatible":
            self.embedder.require_configured()
        job = session.scalar(select(Job).where(Job.version_id == version.id))
        version.embedding_profile = self.embedder.profile
        version.status, version.error = "queued", ""
        job.status, job.error, job.lease_until, job.claim = "queued", "", 0, ""
        return {"id": doc.id, "document_id": doc.id, "job_id": job.id, "status": "queued"}
