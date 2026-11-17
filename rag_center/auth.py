import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .config import Settings
from .db import Database
from .errors import DomainError, require
from .models import ApiKey, LoginSession, RateBucket, User

SCOPES = {"read", "create", "update", "delete", "download", "kb:create"}


def digest(value: str, secret: str):
    return hmac.new(secret.encode(), value.encode(), hashlib.sha256).hexdigest()


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    value = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1)
    return f"scrypt$16384$8$1${salt}${value.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, n, r, p, salt, expected = encoded.split("$")
        if algorithm != "scrypt" or (n, r, p) != ("16384", "8", "1"):
            return False
        actual = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1)
        return hmac.compare_digest(actual.hex(), expected)
    except (ValueError, TypeError):
        return False


_DUMMY_PASSWORD_HASH = hash_password(secrets.token_urlsafe(32))


@dataclass(frozen=True)
class Principal:
    """仅表示已认证的身份；每次操作都必须重新读取密钥的当前授权。"""

    user_id: str
    actor_id: str
    key_id: str | None = None
    csrf: str | None = None


def rate_limit(session: Session, key: str, limit: int, window: int) -> None:
    """立即保存尝试次数，即使后续业务操作失败也保留计数。

    此函数会提交传入的数据库会话，只能在业务变更开始前调用；
    否则后续校验失败时，可能出现部分资源已经提交的情况。
    """
    # 使用原子插入或更新，避免多个 API 进程先读取再递增造成竞争。
    if session.bind.dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    now = time.time()
    bucket = f"{key}:{int(now // window)}"
    stmt = insert(RateBucket).values(key=bucket, count=1, expires_at=now + window)
    count = session.scalar(
        stmt.on_conflict_do_update(
            index_elements=[RateBucket.key], set_={"count": RateBucket.count + 1}
        ).returning(RateBucket.count)
    )
    session.commit()
    require(count <= limit, "rate_limited", "请求过于频繁，请稍后再试", 429)


class Auth:
    """处理账号注册、密码登录及可分别撤销的会话和 API 凭证。"""

    def __init__(self, database: Database, settings: Settings):
        self.db, self.settings = database, settings

    @staticmethod
    def user_info(user: User, csrf: str) -> dict:
        return {"id": user.id, "phone": user.phone, "username": user.username, "csrf": csrf}

    def _issue_session(self, session: Session, user: User) -> tuple[str, dict]:
        """与账号创建一起提交，复用同一套会话及 CSRF 机制。"""
        token, csrf = secrets.token_urlsafe(40), secrets.token_urlsafe(24)
        session.add(
            LoginSession(
                digest=digest(token, self.settings.secret),
                user_id=user.id,
                csrf=csrf,
                expires_at=time.time() + self.settings.session_ttl,
            )
        )
        session.commit()
        return token, self.user_info(user, csrf)

    def register(self, username: str, password: str, ip: str) -> tuple[str, dict]:
        with self.db.sessions() as session:
            rate_limit(session, f"register:{ip}", 10, 3600)
            require(
                not session.scalar(select(User.id).where(User.username == username)),
                "username_taken",
                "账号已存在，请使用其他账号",
                409,
            )
            user = User(username=username, password_hash=hash_password(password))
            session.add(user)
            try:
                session.flush()
                return self._issue_session(session, user)
            except IntegrityError:
                session.rollback()
                if session.scalar(select(User.id).where(User.username == username)):
                    raise DomainError("username_taken", "账号已存在，请使用其他账号", 409) from None
                raise

    def login(self, username: str, password: str, ip: str) -> tuple[str, dict]:
        with self.db.sessions() as session:
            rate_limit(session, f"login:{ip}", 60, 600)
            rate_limit(session, f"login-account:{digest(username, self.settings.secret)}", 20, 600)
            user = session.scalar(select(User).where(User.username == username))
            encoded = user.password_hash if user and user.password_hash else _DUMMY_PASSWORD_HASH
            valid = verify_password(password, encoded)
            require(user and user.password_hash and valid, "invalid_credentials", "账号或密码错误", 401)
            return self._issue_session(session, user)

    def authenticate(self, session_token=None, bearer=None):
        with self.db.sessions() as s:
            if bearer:
                key = s.scalar(select(ApiKey).where(ApiKey.digest == digest(bearer, self.settings.secret)))
                require(
                    key and not key.revoked and (not key.expires_at or key.expires_at > time.time()),
                    "unauthorized",
                    "密钥无效、已撤销或已过期",
                    401,
                )
                key.last_used_at = time.time()
                s.commit()
                return Principal(key.user_id, f"key:{key.id}", key_id=key.id)
            token = s.get(LoginSession, digest(session_token or "", self.settings.secret))
            require(token and token.expires_at > time.time(), "unauthorized", "请先登录", 401)
            return Principal(token.user_id, f"user:{token.user_id}", csrf=token.csrf)

    def logout(self, token):
        with self.db.sessions() as s:
            row = s.get(LoginSession, digest(token or "", self.settings.secret))
            if row:
                s.delete(row)
            s.commit()
