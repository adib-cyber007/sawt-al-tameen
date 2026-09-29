"""Durable media admission and atomic, replayable voice operations."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "twilio_media_admissions",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column("call_sid", sa.String(100), nullable=False, unique=True),
        sa.Column("stream_sid", sa.String(100), nullable=False, unique=True),
        sa.Column("expires_at", sa.BigInteger(), nullable=False),
        sa.Column("admitted_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "voice_tool_executions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("actor_id", sa.String(100), nullable=False),
        sa.Column("conversation_id", sa.String(100), nullable=False),
        sa.Column("tool_name", sa.String(64), nullable=False),
        sa.Column("operation_key", sa.String(64), nullable=False),
        sa.Column("arguments_hash", sa.String(64), nullable=False),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("response", sa.JSON().with_variant(postgresql.JSONB(), "postgresql")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("actor_id", "conversation_id", "tool_name", "operation_key"),
    )
    op.create_table(
        "voice_provider_calls",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("actor_id", sa.String(100), nullable=False),
        sa.Column("conversation_id", sa.String(100), nullable=False),
        sa.Column("provider_call_id", sa.String(100), nullable=False),
        sa.Column("execution_id", sa.String(36), sa.ForeignKey("voice_tool_executions.id"), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.UniqueConstraint("actor_id", "conversation_id", "provider_call_id"),
    )


def downgrade():
    op.drop_table("voice_provider_calls")
    op.drop_table("voice_tool_executions")
    op.drop_table("twilio_media_admissions")
