"""Remember budget dates the household set by hand, so automatic card-charge counting never overrides them."""

from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    # Plain ADD COLUMN: a batch table rebuild would drop the table's unnamed CHECK constraints.
    op.add_column(
        "transactions",
        sa.Column("allocation_manual", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.execute(
        """
        UPDATE transactions SET allocation_manual = 1 WHERE id IN (
            SELECT entity_id FROM audit_events
            WHERE entity_type = 'transaction' AND action = 'correct'
              AND json_extract(before, '$.allocation_date') IS NOT json_extract(after, '$.allocation_date')
        )
        """
    )


def downgrade():
    op.execute("ALTER TABLE transactions DROP COLUMN allocation_manual")
