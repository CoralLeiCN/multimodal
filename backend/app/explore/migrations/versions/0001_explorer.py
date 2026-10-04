"""User-owned collection conversations and immutable run events."""

import sqlalchemy as sa
from alembic import op

revision = "0001_explorer"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "explorer_conversations",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("owner", sa.String(200), nullable=False),
        sa.Column("title", sa.String(120), nullable=False),
        sa.Column("thread_id", sa.String(100)),
        sa.Column("active_run", sa.String(32)),
        sa.Column("created_at", sa.Float, nullable=False),
    )
    op.create_index(
        "ix_explorer_conversations_owner", "explorer_conversations", ["owner"]
    )
    op.create_table(
        "explorer_runs",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "conversation_id",
            sa.String(32),
            sa.ForeignKey("explorer_conversations.id"),
            nullable=False,
        ),
        sa.Column("request_key", sa.String(100), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("upload_id", sa.String(32)),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("answer", sa.Text, nullable=False),
        sa.Column("results", sa.JSON, nullable=False),
        sa.Column("evidence", sa.JSON, nullable=False),
        sa.Column("usage", sa.JSON, nullable=False),
        sa.Column("error", sa.String(100)),
        sa.Column("index_version", sa.String(100)),
        sa.Column("deadline", sa.Float),
        sa.Column("token_hash", sa.String(64)),
        sa.Column("tool_calls", sa.Integer, nullable=False),
        sa.Column("created_at", sa.Float, nullable=False),
        sa.UniqueConstraint("conversation_id", "request_key"),
    )
    op.create_index(
        "ix_explorer_runs_conversation_id", "explorer_runs", ["conversation_id"]
    )
    op.create_table(
        "explorer_events",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "run_id", sa.String(32), sa.ForeignKey("explorer_runs.id"), nullable=False
        ),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("data", sa.JSON, nullable=False),
    )
    op.create_index("ix_explorer_events_run_id", "explorer_events", ["run_id"])
    op.create_table(
        "explorer_uploads",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("owner", sa.String(200), nullable=False),
        sa.Column("mime", sa.String(50), nullable=False),
        sa.Column("checksum", sa.String(64), nullable=False),
        sa.Column("created_at", sa.Float, nullable=False),
    )
    op.create_index("ix_explorer_uploads_owner", "explorer_uploads", ["owner"])


def downgrade():
    for table in (
        "explorer_events",
        "explorer_runs",
        "explorer_uploads",
        "explorer_conversations",
    ):
        op.drop_table(table)
