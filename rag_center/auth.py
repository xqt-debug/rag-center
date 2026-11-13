import hashlib
import hmac
import re
import secrets
import time
from dataclasses import dataclass

import httpx
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .config import Settings
from .db import Database
from .errors import DomainError, require
from .models import OTP, ApiKey, LoginSession, RateBucket, User

SCOPES = {"read", "create", "update", "delete", "download", "kb:create"}


def digest(value: str, secret: str):
    return hmac.new(secret.encode(), value.encode(), hashlib.sha256).hexdigest()


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
    """处理开发账号登录、手机验证码及可分别撤销的会话和 API 凭证。"""

    def __init__(self, database: Database, settings: Settings):
        self.db, self.settings = database, settings

    @staticmethod
    def user_info(user: User, csrf: str) -> dict:
        return {"id": user.id, "phone": user.phone, "username": user.username, "csrf": csrf}

    def _issue_session(self, session: Session, user: User) -> tuple[str, dict]:
        """与账号创建或验证码消费一起提交，复用同一套会话及 CSRF 机制。"""
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

    def login_development(self, username: str, password: str, ip: str) -> tuple[str, dict]:
        """只接受配置中的测试账号，不提供注册或正式环境密码登录。"""
        require(self.settings.env == "development", "not_found", "登录方式不可用", 404)
        with self.db.sessions() as session:
            rate_limit(session, f"login:{ip}", 60, 600)
            # 对固定长度摘要做比较，避免中文输入异常以及明文长度影响比较过程。
            name_matches = hmac.compare_digest(
                digest(username, self.settings.secret),
                digest(self.settings.dev_username, self.settings.secret),
            )
            password_matches = hmac.compare_digest(
                digest(password, self.settings.secret),
                digest(self.settings.dev_password.get_secret_value(), self.settings.secret),
            )
            require(name_matches and password_matches, "invalid_credentials", "账号或密码错误", 401)
            if session.bind.dialect.name == "postgresql":
                from sqlalchemy.dialects.postgresql import insert
            else:
                from sqlalchemy.dialects.sqlite import insert
            # 并发首次登录只创建一次账号，后续登录继续访问同一份知识库数据。
            session.execute(
                insert(User).values(username=username).on_conflict_do_nothing(index_elements=[User.username])
            )
            user = session.scalar(select(User).where(User.username == username))
            return self._issue_session(session, user)

    def send_code(self, phone, ip):
        require(bool(re.fullmatch(r"1[3-9]\d{9}", phone)), "invalid_phone", "第一版支持中国大陆手机号")
        with self.db.sessions() as s:
            rate_limit(s, f"sms-ip:{ip}", 20, 3600)
            rate_limit(s, f"sms-phone:{phone}", 10, 86400)
            rate_limit(s, f"sms-cooldown:{phone}", 1, 60)
            code = f"{secrets.randbelow(1000000):06d}"
            now = time.time()
            otp = s.get(OTP, phone)
            require(not otp or now - otp.sent_at >= 60, "rate_limited", "请在 60 秒后重新获取验证码", 429)
            if not otp:
                otp = OTP(phone=phone)
                s.add(otp)
            otp.digest = digest(f"{phone}:{code}", self.settings.secret)
            otp.sent_at, otp.expires_at = now, now + self.settings.otp_ttl
            otp.consumed, otp.attempts = False, 0
            if self.settings.sms_provider == "webhook":
                try:
                    with httpx.Client(timeout=10) as client:
                        response = client.post(
                            self.settings.sms_webhook_url,
                            json={"phone": phone, "code": code, "expires_in": self.settings.otp_ttl},
                            headers={"Authorization": f"Bearer {self.settings.sms_webhook_token}"},
                        )
                        response.raise_for_status()
                except httpx.HTTPError:
                    raise DomainError("sms_unavailable", "短信发送失败，请稍后再试", 503) from None
            else:
                require(self.settings.env != "production", "configuration_error", "短信服务未配置", 503)
            s.commit()
            result = {"sent": True, "expires_in": self.settings.otp_ttl}
            if self.settings.sms_provider == "development":
                result["development_code"] = code
            return result

    def login(self, phone, code, ip):
        with self.db.sessions() as s:
            rate_limit(s, f"login:{ip}", 60, 600)
            otp = s.scalar(select(OTP).where(OTP.phone == phone).with_for_update())
            valid = otp and not otp.consumed and otp.expires_at > time.time() and otp.attempts < 5
            require(valid, "invalid_code", "验证码无效或已过期", 401)
            otp.attempts += 1
            if not hmac.compare_digest(otp.digest, digest(f"{phone}:{code}", self.settings.secret)):
                s.commit()
                raise DomainError("invalid_code", "验证码错误", 401)
            # 通过条件更新消费验证码，也保护 SQLite 开发模式下的并发请求。
            s.flush()
            consumed = s.execute(
                update(OTP).where(OTP.phone == phone, OTP.consumed.is_(False)).values(consumed=True)
            )
            require(consumed.rowcount == 1, "invalid_code", "验证码已被使用", 401)
            user = s.scalar(select(User).where(User.phone == phone))
            if not user:
                user = User(phone=phone)
                s.add(user)
                s.flush()
            return self._issue_session(s, user)

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
