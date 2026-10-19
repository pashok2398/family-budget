"""Initial normalized financial ledger (frozen schema)."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import UniqueConstraint, CheckConstraint

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "accounts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("institution", sa.String(), nullable=False),
        sa.Column("currency", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
    )
    op.create_table(
        "credit_cards",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("account_id", sa.String(), sa.ForeignKey("accounts.id"), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("last_four", sa.String(), nullable=True),
    )
    op.create_table(
        "source_documents",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("sha256", sa.String(64), nullable=False, unique=True),
        sa.Column("extension", sa.String(), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "import_batches",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("document_id", sa.String(), sa.ForeignKey("source_documents.id"), nullable=False),
        sa.Column("account_id", sa.String(), sa.ForeignKey("accounts.id"), nullable=False),
        sa.Column("card_id", sa.String(), sa.ForeignKey("credit_cards.id"), nullable=True),
        sa.Column("source_key", sa.String(), nullable=False),
        sa.Column("parser", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("summary", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        UniqueConstraint("document_id", "source_key"),
    )
    op.create_table(
        "import_rows",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("batch_id", sa.String(), sa.ForeignKey("import_batches.id"), nullable=False),
        sa.Column("row_number", sa.Integer(), nullable=False),
        sa.Column("page", sa.Integer(), nullable=True),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("error", sa.String(), nullable=True),
        sa.Column("candidates", sa.JSON(), nullable=False),
        sa.Column("resolution", sa.String(), nullable=True),
        sa.Column("fingerprint", sa.String(), nullable=True),
        sa.Column("base_fingerprint", sa.String(), nullable=True),
        sa.Column("source_reference", sa.String(), nullable=True),
        sa.Column("transaction_id", sa.String(), sa.ForeignKey("transactions.id"), nullable=True),
        UniqueConstraint("batch_id", "row_number"),
    )
    op.create_table(
        "categories",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("code", sa.String(), nullable=False, unique=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
    )
    op.create_table(
        "projects",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("budget_minor", sa.Integer(), nullable=False),
        sa.Column("scope", sa.String(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
    )
    op.create_table(
        "income_sources",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("expected_minor", sa.Integer(), nullable=True),
        sa.Column("provisional", sa.Boolean(), nullable=False),
    )
    op.create_table(
        "transactions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("account_id", sa.String(), sa.ForeignKey("accounts.id"), nullable=False),
        sa.Column("card_id", sa.String(), sa.ForeignKey("credit_cards.id"), nullable=True),
        sa.Column("document_id", sa.String(), sa.ForeignKey("source_documents.id"), nullable=False),
        sa.Column("source_row", sa.Integer(), nullable=False),
        sa.Column("source_page", sa.Integer(), nullable=True),
        sa.Column("source_reference", sa.String(), nullable=True),
        sa.Column("source_key", sa.String(), nullable=False),
        sa.Column("fingerprint", sa.String(), nullable=False, unique=True),
        sa.Column("base_fingerprint", sa.String(), nullable=False),
        sa.Column("transaction_date", sa.Date(), nullable=False),
        sa.Column("posting_date", sa.Date(), nullable=False),
        sa.Column("allocation_date", sa.Date(), nullable=False),
        sa.Column("merchant", sa.String(), nullable=False),
        sa.Column("raw_description", sa.Text(), nullable=False),
        sa.Column("amount_minor", sa.Integer(), nullable=False),
        sa.Column("original_amount_minor", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(), nullable=False),
        sa.Column("original_currency", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("scope", sa.String(), nullable=False),
        sa.Column("category_id", sa.String(), sa.ForeignKey("categories.id"), nullable=True),
        sa.Column("project_id", sa.String(), sa.ForeignKey("projects.id"), nullable=True),
        sa.Column("income_source_id", sa.String(), sa.ForeignKey("income_sources.id"), nullable=True),
        sa.Column("reconciliation_status", sa.String(), nullable=False),
        sa.Column("classification_confidence", sa.Integer(), nullable=False),
        sa.Column("classification_provenance", sa.String(), nullable=False),
        sa.Column("review_status", sa.String(), nullable=False),
        sa.Column("manual", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("purchase_id", sa.String(), nullable=True),
        sa.Column("installment_number", sa.Integer(), nullable=True),
        sa.Column("installment_count", sa.Integer(), nullable=True),
        sa.Column("original_purchase_minor", sa.Integer(), nullable=True),
        sa.Column("remaining_commitment_minor", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        UniqueConstraint("source_key", "source_reference"),
        UniqueConstraint("source_key", "purchase_id", "installment_number"),
        CheckConstraint("classification_confidence BETWEEN 0 AND 100"),
        CheckConstraint(
            "installment_number IS NULL OR (installment_number > 0 AND installment_count >= installment_number)"
        ),
    )
    op.create_table(
        "transaction_splits",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("transaction_id", sa.String(), sa.ForeignKey("transactions.id"), nullable=False),
        sa.Column("category_id", sa.String(), sa.ForeignKey("categories.id"), nullable=False),
        sa.Column("amount_minor", sa.Integer(), nullable=False),
        sa.Column("scope", sa.String(), nullable=False),
        sa.Column("project_id", sa.String(), sa.ForeignKey("projects.id"), nullable=True),
    )
    op.create_table(
        "classification_rules",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("conditions", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("confirmed", sa.Boolean(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
    )
    op.create_table(
        "classification_decisions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("transaction_id", sa.String(), sa.ForeignKey("transactions.id"), nullable=False),
        sa.Column("rule_id", sa.String(), sa.ForeignKey("classification_rules.id"), nullable=True),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("confidence", sa.Integer(), nullable=False),
        sa.Column("provenance", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "transaction_matches",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("members", sa.JSON(), nullable=False),
        sa.Column("explanation", sa.String(), nullable=False),
        sa.Column("before", sa.JSON(), nullable=False),
        sa.Column("after_versions", sa.JSON(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("reversed_at", sa.DateTime(), nullable=True),
    )
    op.create_table(
        "budgets",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("month", sa.String(), nullable=False, unique=True),
        sa.Column("income_minor", sa.Integer(), nullable=False),
        sa.Column("provisional", sa.Boolean(), nullable=False),
    )
    op.create_table(
        "budget_allocations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("budget_id", sa.String(), sa.ForeignKey("budgets.id"), nullable=False),
        sa.Column("category_id", sa.String(), sa.ForeignKey("categories.id"), nullable=False),
        sa.Column("amount_minor", sa.Integer(), nullable=False),
        UniqueConstraint("budget_id", "category_id"),
        CheckConstraint("amount_minor >= 0"),
    )
    op.create_table(
        "sinking_funds",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("category_id", sa.String(), sa.ForeignKey("categories.id"), nullable=False),
        sa.Column("monthly_target_minor", sa.Integer(), nullable=False),
    )
    op.create_table(
        "fund_movements",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("fund_id", sa.String(), sa.ForeignKey("sinking_funds.id"), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("amount_minor", sa.Integer(), nullable=False),
        sa.Column(
            "transaction_id", sa.String(), sa.ForeignKey("transactions.id"), nullable=True, unique=True
        ),
        sa.Column("note", sa.String(), nullable=False),
        sa.Column("request_key", sa.String(), nullable=False, unique=True),
    )
    op.create_table(
        "reimbursements",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("expense_id", sa.String(), sa.ForeignKey("transactions.id"), nullable=False, unique=True),
        sa.Column("refund_id", sa.String(), sa.ForeignKey("transactions.id"), nullable=True, unique=True),
        sa.Column("match_id", sa.String(), sa.ForeignKey("transaction_matches.id"), nullable=True),
        sa.Column("expected_minor", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("note", sa.String(), nullable=False),
    )
    op.create_table(
        "loans",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("principal_minor", sa.Integer(), nullable=True),
        sa.Column("monthly_payment_minor", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(), nullable=False),
    )
    op.create_table(
        "loan_payments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("loan_id", sa.String(), sa.ForeignKey("loans.id"), nullable=False),
        sa.Column(
            "transaction_id", sa.String(), sa.ForeignKey("transactions.id"), nullable=False, unique=True
        ),
        sa.Column("principal_minor", sa.Integer(), nullable=False),
        sa.Column("interest_minor", sa.Integer(), nullable=False),
        CheckConstraint("principal_minor >= 0 AND interest_minor >= 0"),
    )
    op.create_table(
        "recurring_expenses",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("category_id", sa.String(), sa.ForeignKey("categories.id"), nullable=False),
        sa.Column("expected_minor", sa.Integer(), nullable=False),
        sa.Column("day_of_month", sa.Integer(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        CheckConstraint("day_of_month BETWEEN 1 AND 28"),
    )
    op.create_table(
        "review_items",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "transaction_id", sa.String(), sa.ForeignKey("transactions.id"), nullable=False, unique=True
        ),
        sa.Column("reason", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "audit_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("entity_type", sa.String(), nullable=False),
        sa.Column("entity_id", sa.String(), nullable=False),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("before", sa.JSON(), nullable=False),
        sa.Column("after", sa.JSON(), nullable=False),
        sa.Column("actor", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_import_rows_fingerprint", "import_rows", ["fingerprint"])
    op.create_index("ix_import_rows_base_fingerprint", "import_rows", ["base_fingerprint"])
    op.create_index("ix_transactions_base_fingerprint", "transactions", ["base_fingerprint"])
    op.create_index("ix_transactions_transaction_date", "transactions", ["transaction_date"])
    op.create_index("ix_transactions_posting_date", "transactions", ["posting_date"])
    op.create_index("ix_transaction_splits_transaction_id", "transaction_splits", ["transaction_id"])
    op.create_index(
        "ix_classification_decisions_transaction_id", "classification_decisions", ["transaction_id"]
    )
    op.create_index("ix_audit_events_entity_id", "audit_events", ["entity_id"])
    op.execute(
        "CREATE TRIGGER audit_events_no_update BEFORE UPDATE ON audit_events BEGIN SELECT RAISE(ABORT, 'Audit history is immutable'); END"
    )
    op.execute(
        "CREATE TRIGGER audit_events_no_delete BEFORE DELETE ON audit_events BEGIN SELECT RAISE(ABORT, 'Audit history is immutable'); END"
    )
    op.execute(
        "CREATE TRIGGER classification_decisions_no_update BEFORE UPDATE ON classification_decisions BEGIN SELECT RAISE(ABORT, 'Audit history is immutable'); END"
    )
    op.execute(
        "CREATE TRIGGER classification_decisions_no_delete BEFORE DELETE ON classification_decisions BEGIN SELECT RAISE(ABORT, 'Audit history is immutable'); END"
    )

    op.create_table(
        "transaction_match_members",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("match_id", sa.String(36), sa.ForeignKey("transaction_matches.id"), nullable=False),
        sa.Column("transaction_id", sa.String(36), sa.ForeignKey("transactions.id"), nullable=False),
        sa.UniqueConstraint("match_id", "transaction_id"),
    )
    op.create_index("ix_transaction_match_members_match_id", "transaction_match_members", ["match_id"])
    op.create_index(
        "ix_transaction_match_members_transaction_id", "transaction_match_members", ["transaction_id"]
    )


def downgrade():
    op.drop_table("transaction_match_members")
    op.execute("DROP TRIGGER IF EXISTS audit_events_no_update")
    op.execute("DROP TRIGGER IF EXISTS audit_events_no_delete")
    op.execute("DROP TRIGGER IF EXISTS classification_decisions_no_update")
    op.execute("DROP TRIGGER IF EXISTS classification_decisions_no_delete")
    op.drop_table("audit_events")
    op.drop_table("review_items")
    op.drop_table("recurring_expenses")
    op.drop_table("loan_payments")
    op.drop_table("loans")
    op.drop_table("reimbursements")
    op.drop_table("fund_movements")
    op.drop_table("sinking_funds")
    op.drop_table("budget_allocations")
    op.drop_table("budgets")
    op.drop_table("transaction_matches")
    op.drop_table("classification_decisions")
    op.drop_table("classification_rules")
    op.drop_table("transaction_splits")
    op.drop_table("transactions")
    op.drop_table("income_sources")
    op.drop_table("projects")
    op.drop_table("categories")
    op.drop_table("import_rows")
    op.drop_table("import_batches")
    op.drop_table("source_documents")
    op.drop_table("credit_cards")
    op.drop_table("accounts")
