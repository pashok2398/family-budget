"""Fixed expenses remember the merchant they are charged under, so a period can tell which ones arrived."""

from alembic import op
import sqlalchemy as sa

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade():
    # Plain ADD COLUMN: a batch table rebuild would drop the unnamed day_of_month CHECK constraint.
    op.add_column("recurring_expenses", sa.Column("merchant", sa.String(), nullable=True))


def downgrade():
    with op.batch_alter_table("recurring_expenses") as batch:
        batch.drop_column("merchant")
