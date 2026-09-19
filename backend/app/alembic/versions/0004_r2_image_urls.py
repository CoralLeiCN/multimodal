"""Record verified, permanent R2 object URLs separately from source identity."""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"


def upgrade():
    op.add_column("images", sa.Column("r2_url", sa.String(), nullable=True))


def downgrade():
    op.drop_column("images", "r2_url")
