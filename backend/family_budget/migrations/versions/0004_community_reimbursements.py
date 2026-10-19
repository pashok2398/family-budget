"""Community projects that reimburse the household, reimbursement payments with allocations, receipts."""

from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    # Plain ADD COLUMN: a batch table rebuild would drop unnamed CHECK constraints.
    op.add_column(
        "projects", sa.Column("reimbursable", sa.Boolean(), nullable=False, server_default=sa.false())
    )
    op.create_table(
        "reimbursement_payments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(), sa.ForeignKey("projects.id"), nullable=False, index=True),
        sa.Column("received_on", sa.Date(), nullable=False),
        sa.Column("amount_minor", sa.Integer(), nullable=False),
        sa.Column("method", sa.String(), nullable=False),
        sa.Column(
            "transaction_id", sa.String(), sa.ForeignKey("transactions.id"), nullable=True, unique=True
        ),
        sa.Column("note", sa.String(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("amount_minor > 0", name="ck_reimbursement_payment_positive"),
        sa.CheckConstraint(
            "method IN ('bank','bit','paybox','cash','other')", name="ck_reimbursement_payment_method"
        ),
    )
    op.create_table(
        "reimbursement_allocations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "payment_id", sa.String(), sa.ForeignKey("reimbursement_payments.id"), nullable=False, index=True
        ),
        sa.Column(
            "reimbursement_id", sa.String(), sa.ForeignKey("reimbursements.id"), nullable=False, index=True
        ),
        sa.Column("amount_minor", sa.Integer(), nullable=False),
        sa.UniqueConstraint("payment_id", "reimbursement_id"),
        sa.CheckConstraint("amount_minor > 0", name="ck_reimbursement_allocation_positive"),
    )
    op.create_table(
        "attachments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "transaction_id", sa.String(), sa.ForeignKey("transactions.id"), nullable=False, index=True
        ),
        sa.Column("filename", sa.String(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("content_type", sa.String(), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )


def downgrade():
    op.drop_table("attachments")
    op.drop_table("reimbursement_allocations")
    op.drop_table("reimbursement_payments")
    op.execute("ALTER TABLE projects DROP COLUMN reimbursable")
