"""Persistent conversations and ordered messages, independent of sandbox lifetime."""

import sqlalchemy as sa
from alembic import op

revision = "agent_0002"
down_revision = "agent_0001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "agent_conversations",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("workspace", sa.String(100), nullable=False),
        sa.Column(
            "brand_version",
            sa.String(32),
            sa.ForeignKey("agent_brand_versions.id"),
            nullable=False,
        ),
        sa.Column("title", sa.String(120), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("asset_ids", sa.JSON(), nullable=False),
        sa.Column("subject_asset_id", sa.String(32)),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
    )
    op.create_index(
        "ix_agent_conversations_workspace", "agent_conversations", ["workspace"]
    )
    op.create_table(
        "agent_messages",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "conversation_id",
            sa.String(32),
            sa.ForeignKey("agent_conversations.id"),
            nullable=False,
        ),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "run_id", sa.String(32), sa.ForeignKey("agent_runs.id"), nullable=False
        ),
        sa.Column("asset_ids", sa.JSON(), nullable=False),
        sa.Column("idempotency_key", sa.String(100)),
        sa.Column("request_hash", sa.String(64)),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.UniqueConstraint("conversation_id", "sequence"),
        sa.UniqueConstraint("conversation_id", "idempotency_key"),
        sa.UniqueConstraint("run_id", "role"),
    )
    op.create_index(
        "ix_agent_messages_conversation_id", "agent_messages", ["conversation_id"]
    )
    op.create_index("ix_agent_messages_run_id", "agent_messages", ["run_id"])


def downgrade():
    op.drop_table("agent_messages")
    op.drop_table("agent_conversations")
