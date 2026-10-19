from datetime import date, datetime, timezone
from uuid import uuid4
from sqlalchemy import String, ForeignKey, UniqueConstraint, CheckConstraint, JSON, Text
from sqlalchemy.orm import Mapped, mapped_column
from .db import Base


def uid():
    return str(uuid4())


def now():
    return datetime.now(timezone.utc)


class Identity:
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)


class Account(Identity, Base):
    __tablename__ = "accounts"
    name: Mapped[str]
    institution: Mapped[str]
    currency: Mapped[str] = mapped_column(default="ILS")
    kind: Mapped[str] = mapped_column(default="bank")


class CreditCard(Identity, Base):
    __tablename__ = "credit_cards"
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"))
    name: Mapped[str]
    last_four: Mapped[str | None]


class SourceDocument(Identity, Base):
    __tablename__ = "source_documents"
    sha256: Mapped[str] = mapped_column(String(64), unique=True)
    extension: Mapped[str]
    size: Mapped[int]
    created_at: Mapped[datetime] = mapped_column(default=now)


class ImportBatch(Identity, Base):
    __tablename__ = "import_batches"
    document_id: Mapped[str] = mapped_column(ForeignKey("source_documents.id"))
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"))
    card_id: Mapped[str | None] = mapped_column(ForeignKey("credit_cards.id"))
    source_key: Mapped[str]
    parser: Mapped[str]
    status: Mapped[str] = mapped_column(default="preview")
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(default=now)
    __table_args__ = (UniqueConstraint("document_id", "source_key"),)


class ImportRow(Identity, Base):
    __tablename__ = "import_rows"
    batch_id: Mapped[str] = mapped_column(ForeignKey("import_batches.id"))
    row_number: Mapped[int]
    page: Mapped[int | None]
    data: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str]
    error: Mapped[str | None]
    candidates: Mapped[list] = mapped_column(JSON, default=list)
    resolution: Mapped[str | None]
    fingerprint: Mapped[str | None] = mapped_column(index=True)
    base_fingerprint: Mapped[str | None] = mapped_column(index=True)
    source_reference: Mapped[str | None]
    transaction_id: Mapped[str | None] = mapped_column(ForeignKey("transactions.id", use_alter=True))
    __table_args__ = (UniqueConstraint("batch_id", "row_number"),)


class Category(Identity, Base):
    __tablename__ = "categories"
    code: Mapped[str] = mapped_column(unique=True)
    name: Mapped[str]
    kind: Mapped[str] = mapped_column(default="expense")


class Project(Identity, Base):
    __tablename__ = "projects"
    name: Mapped[str]
    budget_minor: Mapped[int] = mapped_column(default=0)
    scope: Mapped[str] = mapped_column(default="project")
    active: Mapped[bool] = mapped_column(default=True)
    reimbursable: Mapped[bool] = mapped_column(
        default=False
    )  # community project that pays the household back


class IncomeSource(Identity, Base):
    __tablename__ = "income_sources"
    name: Mapped[str]
    expected_minor: Mapped[int | None]
    provisional: Mapped[bool] = mapped_column(default=True)


class Transaction(Identity, Base):
    __tablename__ = "transactions"
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"))
    card_id: Mapped[str | None] = mapped_column(ForeignKey("credit_cards.id"))
    document_id: Mapped[str] = mapped_column(ForeignKey("source_documents.id"))
    source_row: Mapped[int]
    source_page: Mapped[int | None]
    source_reference: Mapped[str | None]
    source_key: Mapped[str]
    fingerprint: Mapped[str] = mapped_column(unique=True)
    base_fingerprint: Mapped[str] = mapped_column(index=True)
    transaction_date: Mapped[date] = mapped_column(index=True)
    posting_date: Mapped[date] = mapped_column(index=True)
    allocation_date: Mapped[date]
    allocation_manual: Mapped[bool] = mapped_column(default=False)  # budget date set by the user
    merchant: Mapped[str]
    raw_description: Mapped[str] = mapped_column(Text)
    amount_minor: Mapped[int]
    original_amount_minor: Mapped[int]
    currency: Mapped[str]
    original_currency: Mapped[str]
    kind: Mapped[str] = mapped_column(default="purchase")
    scope: Mapped[str] = mapped_column(default="household")
    category_id: Mapped[str | None] = mapped_column(ForeignKey("categories.id"))
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id"))
    income_source_id: Mapped[str | None] = mapped_column(ForeignKey("income_sources.id"))
    reconciliation_status: Mapped[str] = mapped_column(default="unmatched")
    classification_confidence: Mapped[int] = mapped_column(default=0)
    classification_provenance: Mapped[str] = mapped_column(default="unclassified")
    review_status: Mapped[str] = mapped_column(default="pending")
    manual: Mapped[bool] = mapped_column(default=False)
    version: Mapped[int] = mapped_column(default=1)
    purchase_id: Mapped[str | None]
    installment_number: Mapped[int | None]
    installment_count: Mapped[int | None]
    original_purchase_minor: Mapped[int | None]
    remaining_commitment_minor: Mapped[int | None]
    created_at: Mapped[datetime] = mapped_column(default=now)
    __table_args__ = (
        UniqueConstraint("source_key", "source_reference"),
        UniqueConstraint("source_key", "purchase_id", "installment_number"),
        CheckConstraint("classification_confidence BETWEEN 0 AND 100"),
        CheckConstraint(
            "installment_number IS NULL OR (installment_number > 0 AND installment_count >= installment_number)"
        ),
    )


class TransactionSplit(Identity, Base):
    __tablename__ = "transaction_splits"
    transaction_id: Mapped[str] = mapped_column(ForeignKey("transactions.id"), index=True)
    category_id: Mapped[str] = mapped_column(ForeignKey("categories.id"))
    amount_minor: Mapped[int]
    scope: Mapped[str]
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id"))


class ClassificationRule(Identity, Base):
    __tablename__ = "classification_rules"
    name: Mapped[str]
    priority: Mapped[int]
    conditions: Mapped[dict] = mapped_column(JSON)
    result: Mapped[dict] = mapped_column(JSON)
    confirmed: Mapped[bool] = mapped_column(default=False)
    active: Mapped[bool] = mapped_column(default=True)


class ClassificationDecision(Identity, Base):
    __tablename__ = "classification_decisions"
    transaction_id: Mapped[str] = mapped_column(ForeignKey("transactions.id"), index=True)
    rule_id: Mapped[str | None] = mapped_column(ForeignKey("classification_rules.id"))
    result: Mapped[dict] = mapped_column(JSON)
    confidence: Mapped[int]
    provenance: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(default=now)


class TransactionMatch(Identity, Base):
    __tablename__ = "transaction_matches"
    kind: Mapped[str]
    members: Mapped[list] = mapped_column(JSON)
    explanation: Mapped[str]
    before: Mapped[dict] = mapped_column(JSON)
    after_versions: Mapped[dict] = mapped_column(JSON)
    active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(default=now)
    reversed_at: Mapped[datetime | None]


class Budget(Identity, Base):
    __tablename__ = "budgets"
    month: Mapped[str] = mapped_column(unique=True)
    income_minor: Mapped[int] = mapped_column(default=3200000)
    provisional: Mapped[bool] = mapped_column(default=True)


class BudgetAllocation(Identity, Base):
    __tablename__ = "budget_allocations"
    budget_id: Mapped[str] = mapped_column(ForeignKey("budgets.id"))
    category_id: Mapped[str] = mapped_column(ForeignKey("categories.id"))
    amount_minor: Mapped[int]
    __table_args__ = (UniqueConstraint("budget_id", "category_id"), CheckConstraint("amount_minor >= 0"))


class SinkingFund(Identity, Base):
    __tablename__ = "sinking_funds"
    name: Mapped[str]
    category_id: Mapped[str] = mapped_column(ForeignKey("categories.id"))
    monthly_target_minor: Mapped[int]


class FundMovement(Identity, Base):
    __tablename__ = "fund_movements"
    fund_id: Mapped[str] = mapped_column(ForeignKey("sinking_funds.id"))
    date: Mapped[date]
    amount_minor: Mapped[int]
    transaction_id: Mapped[str | None] = mapped_column(ForeignKey("transactions.id"), unique=True)
    note: Mapped[str]
    request_key: Mapped[str] = mapped_column(unique=True)


class Reimbursement(Identity, Base):
    __tablename__ = "reimbursements"
    expense_id: Mapped[str] = mapped_column(ForeignKey("transactions.id"), unique=True)
    refund_id: Mapped[str | None] = mapped_column(ForeignKey("transactions.id"), unique=True)
    match_id: Mapped[str | None] = mapped_column(ForeignKey("transaction_matches.id"))
    expected_minor: Mapped[int]
    status: Mapped[str] = mapped_column(default="pending")
    note: Mapped[str]


class Loan(Identity, Base):
    __tablename__ = "loans"
    name: Mapped[str]
    principal_minor: Mapped[int | None]
    monthly_payment_minor: Mapped[int]
    currency: Mapped[str] = mapped_column(default="ILS")


class LoanPayment(Identity, Base):
    __tablename__ = "loan_payments"
    loan_id: Mapped[str] = mapped_column(ForeignKey("loans.id"))
    transaction_id: Mapped[str] = mapped_column(ForeignKey("transactions.id"), unique=True)
    principal_minor: Mapped[int]
    interest_minor: Mapped[int]
    __table_args__ = (CheckConstraint("principal_minor >= 0 AND interest_minor >= 0"),)


class RecurringExpense(Identity, Base):
    __tablename__ = "recurring_expenses"
    name: Mapped[str]
    category_id: Mapped[str] = mapped_column(ForeignKey("categories.id"))
    expected_minor: Mapped[int]
    day_of_month: Mapped[int]
    active: Mapped[bool] = mapped_column(default=True)
    merchant: Mapped[str | None]  # the transaction name it arrives under; None: never matched
    __table_args__ = (CheckConstraint("day_of_month BETWEEN 1 AND 28"),)


class ReviewItem(Identity, Base):
    __tablename__ = "review_items"
    transaction_id: Mapped[str] = mapped_column(ForeignKey("transactions.id"), unique=True)
    reason: Mapped[str]
    status: Mapped[str] = mapped_column(default="open")
    created_at: Mapped[datetime] = mapped_column(default=now)


class AuditEvent(Identity, Base):
    __tablename__ = "audit_events"
    entity_type: Mapped[str]
    entity_id: Mapped[str] = mapped_column(index=True)
    action: Mapped[str]
    before: Mapped[dict] = mapped_column(JSON)
    after: Mapped[dict] = mapped_column(JSON)
    actor: Mapped[str] = mapped_column(default="local-user")
    created_at: Mapped[datetime] = mapped_column(default=now)


class MatchMember(Identity, Base):
    __tablename__ = "transaction_match_members"
    match_id: Mapped[str] = mapped_column(ForeignKey("transaction_matches.id"), index=True)
    transaction_id: Mapped[str] = mapped_column(ForeignKey("transactions.id"), index=True)
    __table_args__ = (UniqueConstraint("match_id", "transaction_id"),)


class AppSetting(Base):
    __tablename__ = "app_settings"
    key: Mapped[str] = mapped_column(primary_key=True)
    value: Mapped[str]


class ReimbursementPayment(Identity, Base):
    """Money a community project paid back, by bank, Bit, PayBox or cash, allocated to its expenses."""

    __tablename__ = "reimbursement_payments"
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    received_on: Mapped[date]
    amount_minor: Mapped[int]
    method: Mapped[str]
    transaction_id: Mapped[str | None] = mapped_column(ForeignKey("transactions.id"), unique=True)
    note: Mapped[str] = mapped_column(default="")
    created_at: Mapped[datetime] = mapped_column(default=now)
    __table_args__ = (
        CheckConstraint("amount_minor > 0", name="ck_reimbursement_payment_positive"),
        CheckConstraint(
            "method IN ('bank','bit','paybox','cash','other')", name="ck_reimbursement_payment_method"
        ),
    )


class ReimbursementAllocation(Identity, Base):
    __tablename__ = "reimbursement_allocations"
    payment_id: Mapped[str] = mapped_column(ForeignKey("reimbursement_payments.id"), index=True)
    reimbursement_id: Mapped[str] = mapped_column(ForeignKey("reimbursements.id"), index=True)
    amount_minor: Mapped[int]
    __table_args__ = (
        UniqueConstraint("payment_id", "reimbursement_id"),
        CheckConstraint("amount_minor > 0", name="ck_reimbursement_allocation_positive"),
    )


class Attachment(Identity, Base):
    """A receipt or invoice file kept with a transaction; stored by content hash outside the database."""

    __tablename__ = "attachments"
    transaction_id: Mapped[str] = mapped_column(ForeignKey("transactions.id"), index=True)
    filename: Mapped[str]
    sha256: Mapped[str] = mapped_column(String(64))
    content_type: Mapped[str]
    size: Mapped[int]
    created_at: Mapped[datetime] = mapped_column(default=now)
