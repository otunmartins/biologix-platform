"""Add experiment job progress and result lifecycle fields."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260802_02"
down_revision = "20260802_01"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("experiments", sa.Column("progress", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("experiments", sa.Column("current_stage", sa.String(120)))
    op.add_column("experiments", sa.Column("progress_log", postgresql.JSONB(), nullable=False, server_default="[]"))
    op.add_column("experiments", sa.Column("error_message", sa.Text()))
    op.add_column("experiments", sa.Column("started_at", sa.DateTime(timezone=True)))
    op.add_column("experiments", sa.Column("completed_at", sa.DateTime(timezone=True)))
    op.add_column("experiments", sa.Column("job_id", sa.String(64)))


def downgrade():
    for column in ("job_id", "completed_at", "started_at", "error_message", "progress_log", "current_stage", "progress"):
        op.drop_column("experiments", column)
