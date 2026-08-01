"""Create account and experiment tables."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260802_01"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    state = sa.Enum("queued", "running", "done", "failed", name="experimentstate")
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)
    op.create_table(
        "experiments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("biologic_target", sa.String(200), nullable=False),
        sa.Column("polymer_target", sa.String(500)),
        sa.Column("description", sa.Text()),
        sa.Column("status", state, nullable=False),
        sa.Column("parameters", postgresql.JSONB(), nullable=False),
        sa.Column("results", postgresql.JSONB()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_experiments_owner_id", "experiments", ["owner_id"])


def downgrade():
    op.drop_table("experiments")
    op.drop_table("users")
    sa.Enum(name="experimentstate").drop(op.get_bind(), checkfirst=True)
