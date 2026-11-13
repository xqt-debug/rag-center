"""记录文档索引实际发布完成的时间，历史版本保留为空。"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("document_versions", sa.Column("indexed_at", sa.Float(), nullable=True))


def downgrade():
    with op.batch_alter_table("document_versions") as batch:
        batch.drop_column("indexed_at")
