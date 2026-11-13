from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RAG_", env_file=".env", extra="ignore")

    env: Literal["development", "production"] = "development"
    database_url: str = "sqlite:///./data/development.sqlite"
    storage_dir: Path = Path("data/files")
    public_url: str = "http://localhost:8000"
    secret: str = "development-only-change-before-deploying"
    # 仅供开发环境使用；生产环境不会注册账号密码测试入口。
    dev_username: str = Field("admin", pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}$")
    dev_password: SecretStr = Field(default=SecretStr("admin123"), min_length=1, max_length=256)
    sms_provider: Literal["development", "webhook"] = "development"
    sms_webhook_url: str = ""
    sms_webhook_token: str = ""
    embedding_provider: Literal["local-test", "openai-compatible"] = "openai-compatible"
    embedding_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    embedding_api_key: str = ""
    embedding_model: str = "text-embedding-v4"
    embedding_dimensions: int = Field(1024, ge=8, le=2000)
    max_file_mb: int = Field(20, gt=0)
    storage_quota_mb: int = Field(200, gt=0)
    max_documents: int = Field(200, gt=0)
    otp_ttl: int = Field(300, gt=0)
    session_ttl: int = Field(604800, gt=0)
    job_lease_seconds: int = Field(300, ge=3)

    def validate_deployment(self):
        if self.env == "production":
            if len(self.secret) < 32 or self.secret.startswith("development"):
                raise ValueError("Production requires a random RAG_SECRET of at least 32 characters")
            if not self.database_url.startswith("postgresql"):
                raise ValueError("Production requires PostgreSQL")
            if self.sms_provider != "webhook" or not self.sms_webhook_url.startswith("https://"):
                raise ValueError("Production requires an HTTPS SMS webhook")
            if self.embedding_provider != "openai-compatible" or not self.embedding_api_key:
                raise ValueError("Production requires a real embedding service")
            if not self.public_url.startswith("https://"):
                raise ValueError("Production requires HTTPS")


@lru_cache
def get_settings():
    return Settings()
