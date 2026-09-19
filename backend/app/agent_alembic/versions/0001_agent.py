"""Initial independent agent schema (frozen at revision creation)."""

import sqlalchemy as sa
from alembic import op

revision = "agent_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "agent_assets",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("workspace", sa.String(length=100), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("object_key", sa.Text(), nullable=False),
        sa.Column("checksum", sa.String(length=64), nullable=False),
        sa.Column("mime", sa.String(length=40), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("source", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_agent_assets_workspace", "agent_assets", ["workspace"], unique=False
    )
    op.create_table(
        "agent_brand_versions",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("workspace", sa.String(length=100), nullable=False),
        sa.Column("brand_id", sa.String(length=32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("profile", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace", "brand_id", "version"),
    )
    op.create_index(
        "ix_agent_brand_versions_workspace",
        "agent_brand_versions",
        ["workspace"],
        unique=False,
    )
    op.create_table(
        "agent_trace_batches",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "agent_workspaces",
        sa.Column("id", sa.String(length=100), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "agent_runs",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("workspace", sa.String(length=100), nullable=False),
        sa.Column("idempotency_key", sa.String(length=100), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("brand_version", sa.String(length=32), nullable=False),
        sa.Column("request", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("stage", sa.String(length=50), nullable=False),
        sa.Column("attempt_id", sa.String(length=32), nullable=True),
        sa.Column("sandbox_id", sa.String(length=100), nullable=True),
        sa.Column("sandbox_name", sa.String(length=100), nullable=True),
        sa.Column("image_version", sa.String(length=64), nullable=True),
        sa.Column("deadline", sa.Float(), nullable=True),
        sa.Column("lease_until", sa.Float(), nullable=False),
        sa.Column("trace_id", sa.String(length=32), nullable=True),
        sa.Column("traceparent", sa.String(length=100), nullable=True),
        sa.Column("checkpoint", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("question", sa.Text(), nullable=True),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("event_sequence", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace", "idempotency_key"),
        sa.ForeignKeyConstraint(["brand_version"], ["agent_brand_versions.id"]),
    )
    op.create_index("ix_agent_runs_status", "agent_runs", ["status"], unique=False)
    op.create_index(
        "ix_agent_runs_workspace", "agent_runs", ["workspace"], unique=False
    )
    op.create_table(
        "agent_events",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("run_id", sa.String(length=32), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=50), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "sequence"),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"]),
    )
    op.create_index("ix_agent_events_run_id", "agent_events", ["run_id"], unique=False)
    op.create_table(
        "agent_provider_calls",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("run_id", sa.String(length=32), nullable=False),
        sa.Column("step_id", sa.String(length=80), nullable=False),
        sa.Column("operation", sa.String(length=30), nullable=False),
        sa.Column("is_revision", sa.Boolean(), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("response", sa.JSON(), nullable=False),
        sa.Column("usage", sa.JSON(), nullable=True),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "step_id"),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"]),
    )
    op.create_index(
        "ix_agent_provider_calls_run_id",
        "agent_provider_calls",
        ["run_id"],
        unique=False,
    )


def downgrade():
    op.drop_table("agent_provider_calls")
    op.drop_table("agent_events")
    op.drop_table("agent_runs")
    op.drop_table("agent_workspaces")
    op.drop_table("agent_trace_batches")
    op.drop_table("agent_brand_versions")
    op.drop_table("agent_assets")
