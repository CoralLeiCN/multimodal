"""Keep the embedding cache outside the PostgreSQL catalogue."""

import sqlalchemy as sa
from alembic import op
from app.models import EmbeddingCache

revision = "0003"
down_revision = "0002"


def upgrade():
    catalogue = op.get_bind()
    if sa.inspect(catalogue).has_table("embedding_cache"):
        if catalogue.scalar(sa.select(sa.func.count()).select_from(EmbeddingCache)):
            raise RuntimeError("Cannot discard a nonempty PostgreSQL embedding cache.")
        op.drop_table("embedding_cache")


def downgrade():
    EmbeddingCache.__table__.create(op.get_bind())
