"""Create the image catalogue and ingestion ledger."""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None


def upgrade():
    op.create_table(
        "index_generations",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("collection", sa.String(), unique=True, nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("model", sa.String(), nullable=False),
        sa.Column("dimensions", sa.Integer(), nullable=False),
        sa.Column("config_hash", sa.String(), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.Column("scanned", sa.Integer(), nullable=False),
        sa.Column("skipped", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("completed_at", sa.String()),
        sa.Column("error", sa.String()),
    )
    op.create_table(
        "images",
        sa.Column(
            "generation_id",
            sa.String(),
            sa.ForeignKey("index_generations.id"),
            primary_key=True,
        ),
        sa.Column("image_id", sa.String(), primary_key=True),
        *[
            sa.Column(name, sa.String(), nullable=False)
            for name in ("location", "relative_path", "checksum", "mime_type", "title")
        ],
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
    )
    op.create_table(
        "image_associations",
        sa.Column("id", sa.String(), primary_key=True),
        *[
            sa.Column(name, sa.String(), nullable=False)
            for name in (
                "generation_id",
                "image_id",
                "record_uid",
                "image_uid",
                "source_json",
                "title",
                "description",
                "date",
                "maker",
                "catalogue_identifiers",
                "licence",
                "copyright",
                "credit",
            )
        ],
        sa.ForeignKeyConstraint(
            ["generation_id", "image_id"], ["images.generation_id", "images.image_id"]
        ),
    )
    op.create_index(
        "ix_image_associations_generation_id", "image_associations", ["generation_id"]
    )
    op.create_index(
        "ix_image_associations_image_id", "image_associations", ["image_id"]
    )
    op.create_table(
        "image_ingestions",
        sa.Column("generation_id", sa.String(), primary_key=True),
        sa.Column("image_id", sa.String(), primary_key=True),
        *[
            sa.Column(name, sa.String(), nullable=False)
            for name in (
                "checksum",
                "config_hash",
                "point_id",
                "collection",
                "status",
                "updated_at",
            )
        ],
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.String()),
        sa.Column("indexed_at", sa.String()),
        sa.ForeignKeyConstraint(
            ["generation_id", "image_id"], ["images.generation_id", "images.image_id"]
        ),
    )
    op.create_table(
        "embedding_cache",
        sa.Column("key", sa.String(), primary_key=True),
        sa.Column("checksum", sa.String(), nullable=False),
        sa.Column("config_hash", sa.String(), nullable=False),
        sa.Column("vector", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
    )
    op.create_table(
        "service_state",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "active_generation",
            sa.String(),
            sa.ForeignKey("index_generations.id"),
            nullable=False,
        ),
    )


def downgrade():
    for name in (
        "service_state",
        "embedding_cache",
        "image_ingestions",
        "image_associations",
        "images",
        "index_generations",
    ):
        op.drop_table(name)
