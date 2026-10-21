"""Track the prepared Hugging Face release and association provenance."""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"

GENERATION_FIELDS = ("dataset_repo_id", "dataset_revision", "dataset_schema_version")
ASSOCIATION_FIELDS = (
    "record_type",
    "source_sha256",
    "source_record_url",
    "association_id",
    "licence_url",
    "rights_json",
    "attribution",
)


def upgrade():
    for table, fields in (
        ("index_generations", GENERATION_FIELDS),
        ("image_associations", ASSOCIATION_FIELDS),
    ):
        for field in fields:
            op.add_column(
                table, sa.Column(field, sa.String(), nullable=False, server_default="")
            )


def downgrade():
    for table, fields in (
        ("image_associations", ASSOCIATION_FIELDS),
        ("index_generations", GENERATION_FIELDS),
    ):
        for field in reversed(fields):
            op.drop_column(table, field)
