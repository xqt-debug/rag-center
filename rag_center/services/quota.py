"""在用户行锁保护下检查存储预留与文件数量限制。"""

import time

from sqlalchemy import func, select

from ..config import Settings
from ..errors import require
from ..models import Document, DocumentVersion, Upload


class QuotaPolicy:
    def __init__(self, settings: Settings):
        self.settings = settings

    def check(self, session, principal, incoming, new_document=False):
        # 通过 run 或 receive_upload 获取的用户行锁，串行处理配额预留。
        used = session.scalar(
            select(func.coalesce(func.sum(DocumentVersion.size), 0))
            .join(Document, Document.id == DocumentVersion.document_id)
            .where(Document.user_id == principal.user_id, Document.deleted_at.is_(None))
        )
        reserved = session.scalar(
            select(func.coalesce(func.sum(Upload.size), 0)).where(
                Upload.user_id == principal.user_id,
                Upload.consumed.is_(False),
                Upload.expires_at > time.time(),
            )
        )
        require(
            used + reserved + incoming <= self.settings.storage_quota_mb * 1024**2,
            "quota_exceeded",
            "存储空间不足",
            413,
        )
        if new_document:
            count = session.scalar(
                select(func.count())
                .select_from(Document)
                .where(Document.user_id == principal.user_id, Document.deleted_at.is_(None))
            )
            require(count < self.settings.max_documents, "quota_exceeded", "文件数量已达到上限", 413)
