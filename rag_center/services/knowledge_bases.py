"""知识库增删改查操作。"""

from __future__ import annotations

import time

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from .. import schemas
from ..auth import Principal
from ..errors import require
from ..models import ApiKey, Document, KnowledgeBase
from .resources import Operations
from .serializers import knowledge_base_json


class KnowledgeBaseOperations(Operations):
    """知识库领域操作，业务事务由 Service.run 统一提交。"""

    def create_knowledge_base(
        self, session: Session, principal: Principal, key: ApiKey | None, args: schemas.KBCreate
    ) -> dict:
        require(args.name.strip(), "invalid_input", "知识库名称不能为空")
        kb = KnowledgeBase(user_id=principal.user_id, name=args.name.strip(), description=args.description)
        session.add(kb)
        session.flush()
        if key:
            key.kb_ids = [*key.kb_ids, kb.id]
        return knowledge_base_json(session, kb)

    def list_knowledge_bases(
        self, session: Session, principal: Principal, key: ApiKey | None, args: schemas.Page
    ) -> dict:
        query = select(KnowledgeBase).where(
            KnowledgeBase.user_id == principal.user_id, KnowledgeBase.deleted.is_(False)
        )
        if key:
            query = query.where(KnowledgeBase.id.in_(key.kb_ids))
        total = session.scalar(select(func.count()).select_from(query.subquery()))
        items = session.scalars(
            query.order_by(KnowledgeBase.created_at.desc()).offset(args.offset).limit(args.limit)
        )
        return {"items": [knowledge_base_json(session, kb) for kb in items], "total": total}

    def get_knowledge_base(
        self, session: Session, principal: Principal, key: ApiKey | None, args: schemas.KBRef
    ) -> dict:
        return knowledge_base_json(
            session, self.access.knowledge_base(session, principal, key, args.knowledge_base_id)
        )

    def update_knowledge_base(
        self, session: Session, principal: Principal, key: ApiKey | None, args: schemas.KBUpdate
    ) -> dict:
        kb = self.access.knowledge_base(session, principal, key, args.knowledge_base_id)
        require(kb.revision == args.expected_revision, "revision_conflict", "知识库已被修改，请重新读取", 409)
        require(args.name.strip(), "invalid_input", "知识库名称不能为空")
        kb.name, kb.description = args.name.strip(), args.description
        kb.revision += 1
        return knowledge_base_json(session, kb)

    def delete_knowledge_base(
        self, session: Session, principal: Principal, key: ApiKey | None, args: schemas.KBDelete
    ) -> dict:
        kb = self.access.knowledge_base(session, principal, key, args.knowledge_base_id)
        require(kb.revision == args.expected_revision, "revision_conflict", "知识库已被修改，请重新读取", 409)
        count = session.execute(
            update(Document)
            .where(Document.kb_id == kb.id, Document.deleted_at.is_(None))
            .values(deleted_at=time.time(), revision=Document.revision + 1)
        ).rowcount
        kb.deleted = True
        kb.revision += 1
        for affected in session.scalars(select(ApiKey).where(ApiKey.user_id == principal.user_id)):
            affected.kb_ids = [i for i in affected.kb_ids if i != kb.id]
        return {"id": kb.id, "deleted": True, "affected_documents": count, "cleanup": "pending"}
