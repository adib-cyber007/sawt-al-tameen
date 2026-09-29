"""Retain caller corrections missing from provider timeline exports."""

from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("voice_text_corrections",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("conversation_id", sa.String(100), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_voice_text_corrections_conversation_id", "voice_text_corrections", ["conversation_id"])
    if op.get_bind().dialect.name == "postgresql":
        op.execute("CREATE TRIGGER trg_voice_text_corrections_append_only BEFORE UPDATE OR DELETE "
                   "ON voice_text_corrections FOR EACH ROW EXECUTE FUNCTION preauth_reject_mutation()")
    else:
        for operation in ("UPDATE", "DELETE"):
            op.execute(f"CREATE TRIGGER trg_voice_text_corrections_no_{operation.lower()} BEFORE {operation} "
                       f"ON voice_text_corrections BEGIN SELECT RAISE(ABORT, 'voice_text_corrections is append-only'); END")


def downgrade():
    op.drop_table("voice_text_corrections")
