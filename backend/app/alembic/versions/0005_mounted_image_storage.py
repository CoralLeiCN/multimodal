"""Use mounted image paths instead of provider-specific object URLs."""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"


def upgrade():
    op.drop_column("images", "r2_url")


def downgrade():
    # Removed storage references cannot be reconstructed by a schema downgrade.
    op.add_column("images", sa.Column("r2_url", sa.String(), nullable=True))
