"""Add experiment artifacts and immutable events."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260802_03"
down_revision = "20260802_02"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "experiment_artifacts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("experiment_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("experiments.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("object_key", sa.String(700), nullable=False, unique=True),
        sa.Column("content_type", sa.String(120), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_experiment_artifacts_experiment_id", "experiment_artifacts", ["experiment_id"])
    op.create_table(
        "experiment_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("experiment_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("experiments.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("stage", sa.String(120), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("progress", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("experiment_id", "sequence", name="uq_experiment_event_sequence"),
    )
    op.create_index("ix_experiment_events_experiment_id", "experiment_events", ["experiment_id"])


def downgrade():
    op.drop_table("experiment_events")
    op.drop_table("experiment_artifacts")
