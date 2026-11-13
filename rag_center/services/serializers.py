"""相关业务操作共用的公开响应结构。"""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Document, DocumentVersion, Job, KnowledgeBase


def knowledge_base_json(session: Session, kb: KnowledgeBase) -> dict:
    count = session.scalar(
        select(func.count())
        .select_from(Document)
        .where(Document.kb_id == kb.id, Document.deleted_at.is_(None))
    )
    return {
        "id": kb.id,
        "name": kb.name,
        "description": kb.description,
        "revision": kb.revision,
        "document_count": count,
        "created_at": kb.created_at,
    }


def index_details(version: DocumentVersion | None, active_profile: str) -> dict:
    profile = version.embedding_profile if version else ""
    kind = (
        "test"
        if profile.startswith("local-test:")
        else "semantic"
        if profile.startswith("openai-compatible:")
        else "unknown"
    )
    ready = bool(version and version.status == "ready" and kind == "semantic" and profile == active_profile)
    return {
        "index_kind": kind,
        "vector_index_ready": ready,
        "needs_reindex": bool(version and version.status == "ready" and not ready),
    }


def document_json(session: Session, doc: Document, active_profile: str) -> dict:
    latest = session.get(DocumentVersion, doc.latest_version) if doc.latest_version else None
    current = session.get(DocumentVersion, doc.current_version) if doc.current_version else None
    job = session.scalar(select(Job).where(Job.version_id == doc.latest_version)) if latest else None
    return {
        "id": doc.id,
        "knowledge_base_id": doc.kb_id,
        "name": doc.name,
        "revision": doc.revision,
        "current_version": doc.current_version,
        "latest_version": doc.latest_version,
        "status": latest.status if latest else "queued",
        "error": latest.error if latest else "",
        "size": (current or latest).size if latest else 0,
        "page_count": current.page_count if current else 0,
        "chunk_count": current.chunk_count if current else 0,
        "created_at": doc.created_at,
        "job_id": job.id if job else None,
        "updated_at": current.created_at if current else None,
        # 只记录实际发布完成的时间，不用上传时间冒充历史文档的索引完成时间。
        "indexed_at": current.indexed_at if current else None,
        # ready 代表处理流程完成；真实向量是否可用，必须检查来源及模型配置。
        **index_details(current or latest, active_profile),
    }
