"""账户、密钥和资源用量操作。"""

from __future__ import annotations

import secrets
import time

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import schemas
from ..auth import SCOPES, Principal, digest
from ..errors import require
from ..models import ApiKey, Audit, Chunk, Document, DocumentVersion, Job
from .resources import Operations


class AccountOperations(Operations):
    """账户领域操作，业务事务由 Service.run 统一提交。"""

    def create_api_key(
        self, session: Session, principal: Principal, key: ApiKey | None, args: schemas.KeyCreate
    ) -> dict:
        require(args.name.strip(), "invalid_input", "密钥名称不能为空")
        require(set(args.scopes) <= SCOPES and bool(args.scopes), "invalid_input", "权限选项无效")
        require(
            args.knowledge_base_ids or "kb:create" in args.scopes,
            "invalid_input",
            "请选择知识库或允许创建知识库",
        )
        for kb_id in args.knowledge_base_ids:
            self.access.knowledge_base(session, principal, None, kb_id)
        raw = "rag_" + secrets.token_urlsafe(36)
        row = ApiKey(
            user_id=principal.user_id,
            name=args.name.strip(),
            prefix=raw[:12],
            digest=digest(raw, self.settings.secret),
            scopes=sorted(set(args.scopes)),
            kb_ids=list(dict.fromkeys(args.knowledge_base_ids)),
            expires_at=time.time() + args.expires_in_days * 86400 if args.expires_in_days else None,
        )
        session.add(row)
        session.flush()
        # 完整密钥只展示一次，因此不能将此结果写入操作回执缓存。
        return {
            "id": row.id,
            "key": raw,
            "prefix": row.prefix,
            "name": row.name,
            "scopes": row.scopes,
            "knowledge_base_ids": row.kb_ids,
        }

    def list_api_keys(
        self, session: Session, principal: Principal, key: ApiKey | None, args: schemas.Input
    ) -> dict:
        rows = session.scalars(
            select(ApiKey).where(ApiKey.user_id == principal.user_id).order_by(ApiKey.created_at.desc())
        )
        return {
            "items": [
                {
                    "id": record.id,
                    "name": record.name,
                    "prefix": record.prefix,
                    "scopes": record.scopes,
                    "knowledge_base_ids": record.kb_ids,
                    "revoked": record.revoked,
                    "expires_at": record.expires_at,
                    "last_used_at": record.last_used_at,
                    "created_at": record.created_at,
                }
                for record in rows
            ]
        }

    def revoke_api_key(
        self, session: Session, principal: Principal, key: ApiKey | None, args: schemas.KeyRef
    ) -> dict:
        row = session.get(ApiKey, args.key_id)
        require(row and row.user_id == principal.user_id, "not_found", "密钥不存在", 404)
        row.revoked = True
        return {"id": row.id, "revoked": True}

    def usage(self, session: Session, principal: Principal, key: ApiKey | None, args: schemas.Input) -> dict:
        docs = list(
            session.scalars(
                select(Document).where(Document.user_id == principal.user_id, Document.deleted_at.is_(None))
            )
        )
        ids = [d.id for d in docs]
        size = session.scalar(
            select(func.coalesce(func.sum(DocumentVersion.size), 0)).where(
                DocumentVersion.document_id.in_(ids)
            )
        )
        chunks = session.scalar(
            select(func.count())
            .select_from(Chunk)
            .join(Document, Chunk.document_id == Document.id)
            .where(Document.id.in_(ids), Chunk.version_id == Document.current_version)
        )
        jobs = list(session.scalars(select(Job).where(Job.document_id.in_(ids))))
        return {
            "documents": len(docs),
            "max_documents": self.settings.max_documents,
            "storage_bytes": size,
            "storage_quota_bytes": self.settings.storage_quota_mb * 1024**2,
            "chunks": chunks,
            "queued": sum(j.status in {"queued", "processing"} for j in jobs),
            "failed": sum(j.status == "failed" for j in jobs),
            "embedding_provider": self.settings.embedding_provider,
            "database": self.db.engine.dialect.name,
        }

    def list_audit_events(
        self, session: Session, principal: Principal, key: ApiKey | None, args: schemas.Page
    ) -> dict:
        rows = session.scalars(
            select(Audit)
            .where(Audit.user_id == principal.user_id)
            .order_by(Audit.created_at.desc())
            .offset(args.offset)
            .limit(args.limit)
        )
        return {
            "items": [
                {
                    "id": record.id,
                    "actor": record.actor_id,
                    "action": record.action,
                    "target_id": record.target_id,
                    "created_at": record.created_at,
                }
                for record in rows
            ]
        }
