"""文件上传与下载操作。"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path

from itsdangerous import BadSignature, SignatureExpired
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import schemas
from ..auth import Principal
from ..errors import DomainError, require
from ..models import ApiKey, DocumentVersion, Upload, User, uid
from .resources import Operations


class TransferOperations(Operations):
    """传输准备由 Service.run 管理事务，直接传输字节的入口自行管理数据库会话。"""

    def prepare_document_upload(
        self, session: Session, principal: Principal, key: ApiKey | None, args: schemas.UploadPrepare
    ) -> dict:
        self.storage.validate_filename(args.filename)
        require(args.size <= self.settings.max_file_mb * 1024**2, "file_too_large", "文件超过大小限制", 413)
        self.quota.check(session, principal, args.size, new_document=not args.document_id)
        upload = Upload(
            user_id=principal.user_id,
            actor_id=principal.actor_id,
            kb_id=args.knowledge_base_id,
            document_id=args.document_id,
            filename=args.filename,
            size=args.size,
            path=str((self.settings.storage_dir / f"{uid()}.upload").resolve()),
            expires_at=time.time() + 900,
        )
        session.add(upload)
        session.flush()
        token = self.signer.dumps({"upload_id": upload.id, "actor": principal.actor_id})
        return {
            "id": upload.id,
            "upload_id": upload.id,
            "upload_url": f"{self.settings.public_url}/api/transfers/upload/{token}",
            "method": "PUT",
            "size": args.size,
            "expires_at": upload.expires_at,
        }

    def receive_upload(self, token, body):
        try:
            data = self.signer.loads(token, max_age=900)
        except (BadSignature, SignatureExpired):
            raise DomainError("upload_expired", "上传链接无效或已过期", 403) from None
        with self.db.sessions() as session:
            upload = session.get(Upload, data["upload_id"])
            require(
                upload and not upload.consumed and upload.expires_at > time.time(),
                "upload_expired",
                "上传会话已失效",
                403,
            )

            principal = Principal(
                upload.user_id,
                upload.actor_id,
                key_id=upload.actor_id.removeprefix("key:") if upload.actor_id.startswith("key:") else None,
            )
            session.scalar(select(User).where(User.id == principal.user_id).with_for_update())
            key = self.access.resolve_key(session, principal)
            self.access.require_scope(key, "update" if upload.document_id else "create")
            self.access.knowledge_base(session, principal, key, upload.kb_id)
            if upload.document_id:
                self.access.document(session, principal, key, upload.document_id)
            require(len(body) == upload.size, "size_mismatch", "上传大小与声明不一致")
            self.storage.validate_content(upload.filename, body)
            # 重试 PUT 时允许重复提交相同字节，但不能修改已上传对象的内容。
            path = Path(upload.path)
            if upload.received:
                require(
                    path.exists()
                    and hashlib.sha256(path.read_bytes()).digest() == hashlib.sha256(body).digest(),
                    "upload_conflict",
                    "上传对象已有不同内容",
                    409,
                )
            else:
                path.write_bytes(body)
                upload.received = True
            session.commit()
            return {"upload_id": upload.id, "received": True}

    def download_document(
        self, session: Session, principal: Principal, key: ApiKey | None, args: schemas.DocRef
    ) -> dict:
        doc = self.access.document(session, principal, key, args.document_id)
        version_id = doc.current_version or doc.latest_version
        require(version_id, "not_found", "原文件不存在", 404)
        version = session.get(DocumentVersion, version_id)
        token = self.signer.dumps(
            {
                "document_id": doc.id,
                "version_id": version_id,
                "user_id": principal.user_id,
                "actor_id": principal.actor_id,
                "key_id": principal.key_id,
            }
        )
        return {
            "url": f"{self.settings.public_url}/api/transfers/download/{token}",
            "expires_in": 300,
            # 替换为不同格式的新版本时，旧版仍可下载；扩展名必须对应实际字节。
            "filename": str(Path(doc.name).with_suffix(Path(version.filename).suffix)),
            "version_id": version_id,
        }

    def resolve_download(self, token):
        # 此链接仅作为短期下载凭证，不能视为永久授权。
        # 每次 GET 都要重新检查密钥撤销、资源删除和当前发布版本。

        try:
            data = self.signer.loads(token, max_age=300)
        except (BadSignature, SignatureExpired):
            raise DomainError("download_expired", "下载链接无效或已过期", 403) from None
        principal = Principal(data["user_id"], data["actor_id"], key_id=data["key_id"])
        with self.db.sessions() as session:
            key = self.access.resolve_key(session, principal)
            self.access.require_scope(key, "download")
            doc = self.access.document(session, principal, key, data["document_id"])
            require(
                data["version_id"] == (doc.current_version or doc.latest_version),
                "version_changed",
                "文件版本已更新，请重新获取下载链接",
                409,
            )
            version = session.get(DocumentVersion, data["version_id"])
            require(version and Path(version.path).is_file(), "not_found", "原文件不存在", 404)
            return version.path, str(Path(doc.name).with_suffix(Path(version.filename).suffix))
