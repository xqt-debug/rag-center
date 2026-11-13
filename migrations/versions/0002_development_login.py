"""为测试账号增加用户名，并允许用户暂不绑定手机号。"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    # SQLite 批量变更会重建 users 表，连接级外键切换由迁移入口统一处理。
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("username", sa.String(64), nullable=True))
        batch.create_unique_constraint("uq_users_username", ["username"])
        batch.alter_column("phone", existing_type=sa.String(20), nullable=True)


def downgrade():
    if op.get_bind().execute(sa.text("SELECT COUNT(*) FROM users WHERE phone IS NULL")).scalar():
        raise RuntimeError("存在未绑定手机号的测试账号，无法安全回退到手机号必填的旧结构")
    with op.batch_alter_table("users") as batch:
        batch.alter_column("phone", existing_type=sa.String(20), nullable=False)
        batch.drop_constraint("uq_users_username", type_="unique")
        batch.drop_column("username")
