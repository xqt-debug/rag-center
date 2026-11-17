"""Replace SMS login with persistent password accounts, preserving owned data."""

import hashlib
import secrets

import sqlalchemy as sa
from alembic import op
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


class LegacyCredentials(BaseSettings):
    # Read the old settings only during migration; never create a default account.
    model_config = SettingsConfigDict(env_prefix="RAG_", env_file=".env", extra="ignore")
    dev_username: str = "admin"
    dev_password: SecretStr = SecretStr("admin123")


def upgrade():
    op.add_column("users", sa.Column("password_hash", sa.String(256), nullable=True))
    connection = op.get_bind()
    credentials = LegacyCredentials()
    salt = secrets.token_hex(16)
    hashed = hashlib.scrypt(
        credentials.dev_password.get_secret_value().encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1
    ).hex()
    connection.execute(
        sa.text("UPDATE users SET password_hash=:password WHERE username=:username"),
        {"username": credentials.dev_username, "password": f"scrypt$16384$8$1${salt}${hashed}"},
    )
    # Old SMS sessions must no longer grant browser access after this transition.
    connection.execute(sa.text("DELETE FROM sessions"))
    op.drop_table("otps")


def downgrade():
    op.create_table(
        "otps",
        sa.Column("phone", sa.String(20), primary_key=True),
        sa.Column("digest", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.Float(), nullable=False),
        sa.Column("sent_at", sa.Float(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("consumed", sa.Boolean(), nullable=False),
    )
    with op.batch_alter_table("users") as batch:
        batch.drop_column("password_hash")
