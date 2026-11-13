"""各类入口共用的资源归属与密钥权限范围检查。"""

import time

from sqlalchemy.orm import Session

from ..auth import Principal
from ..errors import require
from ..models import ApiKey, Document, KnowledgeBase


class AccessPolicy:
    """重新检查密钥的实时状态；Principal 只代表身份，不能作为缓存的权限授权。"""

    def resolve_key(self, session: Session, principal: Principal) -> ApiKey | None:
        if not principal.key_id:
            return None
        key = session.get(ApiKey, principal.key_id)
        require(
            key
            and key.user_id == principal.user_id
            and not key.revoked
            and (not key.expires_at or key.expires_at > time.time()),
            "unauthorized",
            "密钥无效或已过期",
            401,
        )
        return key

    @staticmethod
    def require_scope(key: ApiKey | None, scope: str) -> None:
        # 没有 API 密钥表示当前主体是已通过网页登录的用户，
        # 可管理其自身资源，但每次资源访问仍必须检查归属。
        require(not key or scope in key.scopes, "forbidden", "密钥未获准执行此操作", 403)

    def knowledge_base(
        self, session: Session, principal: Principal, key: ApiKey | None, kb_id: str, deleted: bool = False
    ) -> KnowledgeBase:
        kb = session.get(KnowledgeBase, kb_id)
        require(
            kb and kb.user_id == principal.user_id and (deleted or not kb.deleted),
            "not_found",
            "知识库不存在或不可访问",
            404,
        )
        require(not key or kb.id in key.kb_ids, "forbidden", "知识库不在密钥授权范围内", 403)
        return kb

    def document(
        self, session: Session, principal: Principal, key: ApiKey | None, doc_id: str, deleted: bool = False
    ) -> Document:
        doc = session.get(Document, doc_id)
        require(
            doc and doc.user_id == principal.user_id and (deleted or not doc.deleted_at),
            "not_found",
            "文件不存在或不可访问",
            404,
        )
        self.knowledge_base(session, principal, key, doc.kb_id, deleted=deleted)
        return doc
