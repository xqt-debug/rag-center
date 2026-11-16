"""检索与原文片段展开操作。"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import schemas
from ..auth import Principal
from ..errors import DomainError, require
from ..media import is_image
from ..models import ApiKey, Chunk, Document, DocumentVersion, KnowledgeBase
from ..retrieval import cosine, tokenize
from .resources import Operations


class SearchOperations(Operations):
    """检索领域操作，业务事务由 Service.run 统一提交。"""

    def _search_bases(self, session, principal, key, ids):
        if ids is not None:
            for kb_id in ids:
                self.access.knowledge_base(session, principal, key, kb_id)
            return ids
        query = select(KnowledgeBase.id).where(
            KnowledgeBase.user_id == principal.user_id, KnowledgeBase.deleted.is_(False)
        )
        if key:
            query = query.where(KnowledgeBase.id.in_(key.kb_ids))
        return list(session.scalars(query))

    def search(
        self, session: Session, principal: Principal, key: ApiKey | None, args: schemas.Search
    ) -> dict:
        require(args.query.strip(), "invalid_input", "查询不能为空")
        kb_ids = self._search_bases(session, principal, key, args.knowledge_base_ids)
        if not kb_ids:
            return {"results": [], "degraded": False, "warnings": []}
        base = (
            select(Chunk)
            .join(Document, Chunk.document_id == Document.id)
            .join(DocumentVersion, Chunk.version_id == DocumentVersion.id)
            .where(
                Document.user_id == principal.user_id,
                Document.kb_id.in_(kb_ids),
                Document.deleted_at.is_(None),
                Chunk.version_id == Document.current_version,
                DocumentVersion.status == "ready",
            )
        )
        ranks, warnings = [], []
        tokens = tokenize(args.query)
        postgres = session.bind.dialect.name == "postgresql"
        if args.mode in {"keyword", "hybrid"}:
            if postgres:
                # 文档与查询分词后都使用 simple 词典，以保留中文词项和编号。
                tsquery = func.plainto_tsquery("simple", " ".join(tokens))
                rows = (
                    list(
                        session.scalars(
                            base.where(Chunk.search_vector.op("@@")(tsquery))
                            .order_by(func.ts_rank_cd(Chunk.search_vector, tsquery).desc(), Chunk.id)
                            .limit(80)
                        )
                    )
                    if tokens
                    else []
                )
            else:
                candidates = list(session.scalars(base))
                wanted = set(tokens)
                scored = [(sum(chunk.tokens.split().count(t) for t in wanted), chunk) for chunk in candidates]
                rows = [
                    chunk
                    for score, chunk in sorted(scored, key=lambda item: (-item[0], item[1].id))
                    if score > 0
                ][:80]
            ranks.append(rows)
        if args.mode in {"semantic", "hybrid"}:
            try:
                vector = self.embedder.embed([args.query])[0]
                semantic_base = base.where(DocumentVersion.embedding_profile == self.embedder.profile)
                if postgres:
                    from pgvector.sqlalchemy import Vector
                    from sqlalchemy import bindparam

                    distance = Chunk.embedding.op("<=>")(bindparam("query_vector", vector, type_=Vector()))
                    rows = list(session.scalars(semantic_base.order_by(distance, Chunk.id).limit(80)))
                else:
                    candidates = list(session.scalars(semantic_base))
                    rows = sorted(candidates, key=lambda chunk: -cosine(chunk.embedding, vector))[:80]
                ranks.append(rows)
            except DomainError:
                if args.mode != "hybrid":
                    raise
                warnings.append("向量服务不可用，本次仅使用关键词检索")
        fused, chunks = {}, {}
        for rows in ranks:
            for position, chunk in enumerate(rows, 1):
                chunks[chunk.id] = chunk
                fused[chunk.id] = fused.get(chunk.id, 0) + 1 / (60 + position)
        results, budget, per_doc = [], 16000, {}
        for chunk_id in sorted(fused, key=lambda i: (-fused[i], i)):
            chunk = chunks[chunk_id]
            if per_doc.get(chunk.document_id, 0) >= 3 or budget <= 0:
                continue
            doc = session.get(Document, chunk.document_id)
            version = session.get(DocumentVersion, chunk.version_id)
            content = chunk.text[:budget]
            results.append(
                {
                    "chunk_id": chunk.id,
                    "document_id": doc.id,
                    "version_id": chunk.version_id,
                    "knowledge_base_id": doc.kb_id,
                    "title": doc.name,
                    "page": chunk.page,
                    "text": content,
                    "citation": f"{doc.name}，第{chunk.page}{'帧' if is_image(version.filename) else '页'}"
                    if chunk.page
                    else f"{doc.name}，片段{chunk.ordinal + 1}",
                }
            )
            budget -= len(content)
            per_doc[doc.id] = per_doc.get(doc.id, 0) + 1
            if len(results) >= args.limit:
                break
        if self.settings.embedding_provider == "local-test" and args.mode != "keyword":
            warnings.append("当前使用开发测试向量，不具备真实语义理解能力")
        return {"results": results, "degraded": any("不可用" in w for w in warnings), "warnings": warnings}

    def get_document_excerpt(
        self, session: Session, principal: Principal, key: ApiKey | None, args: schemas.Excerpt
    ) -> dict:
        doc = self.access.document(session, principal, key, args.document_id)
        chunk = session.get(Chunk, args.chunk_id)
        require(chunk and chunk.document_id == doc.id, "not_found", "片段不存在或不可访问", 404)
        require(chunk.version_id == doc.current_version, "version_changed", "文件已更新，请重新检索", 409)
        rows = session.scalars(
            select(Chunk)
            .where(
                Chunk.version_id == doc.current_version,
                Chunk.ordinal.between(chunk.ordinal - args.context, chunk.ordinal + args.context),
            )
            .order_by(Chunk.ordinal)
        )
        return {
            "document_id": doc.id,
            "version_id": doc.current_version,
            "chunks": [{"chunk_id": chunk.id, "page": chunk.page, "text": chunk.text} for chunk in rows],
        }
