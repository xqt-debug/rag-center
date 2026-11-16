"""持久化图片配套文字，保留既有文件和索引。"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("document_versions", sa.Column("provided_text", sa.Text(), nullable=True))


def downgrade():
    with op.batch_alter_table("document_versions") as batch:
        batch.drop_column("provided_text")
