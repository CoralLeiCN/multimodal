"""Preserve source labels and normalized filter metadata."""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"


def upgrade():
    op.add_column(
        "images",
        sa.Column("filter_metadata", sa.JSON(), nullable=False, server_default="[]"),
    )
    for name in ("places", "categories", "date_ranges"):
        op.add_column(
            "image_associations",
            sa.Column(name, sa.JSON(), nullable=False, server_default="[]"),
        )


def downgrade():
    for name in ("places", "categories", "date_ranges"):
        op.drop_column("image_associations", name)
    op.drop_column("images", "filter_metadata")
