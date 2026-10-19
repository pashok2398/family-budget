from sqlalchemy import select, delete
from ..models import (
    Transaction,
    TransactionSplit,
    Category,
    Project,
    IncomeSource,
    ReviewItem,
    ClassificationDecision,
    ClassificationRule,
    AuditEvent,
    FundMovement,
    LoanPayment,
    Reimbursement,
)
from ..normalization.money import minor, normalized
from .. import receivables
from ..reconciliation.engine import Conflict

FIELDS = (
    "category_id",
    "scope",
    "kind",
    "project_id",
    "income_source_id",
    "allocation_date",
    "merchant",
    "review_status",
    "manual",
    "classification_confidence",
    "classification_provenance",
)


def snapshot(session, transaction):
    state = {
        field: str(getattr(transaction, field)) if field == "allocation_date" else getattr(transaction, field)
        for field in FIELDS
    }
    state["splits"] = [
        {
            "category_id": s.category_id,
            "scope": s.scope,
            "project_id": s.project_id,
            "amount_minor": s.amount_minor,
        }
        for s in session.scalars(
            select(TransactionSplit).where(TransactionSplit.transaction_id == transaction.id)
        )
    ]
    reimbursement = session.scalar(select(Reimbursement).where(Reimbursement.expense_id == transaction.id))
    state["reimbursement_expected"] = reimbursement is not None
    state["version"] = transaction.version
    return state


def correct(session, correction):
    t = session.get(Transaction, correction.id)
    if not t:
        raise ValueError("Unknown transaction")
    if t.version != correction.version:
        raise Conflict("Transaction changed; reload before saving")
    if t.reconciliation_status == "matched":
        raise Conflict("Undo reconciliation before editing its members")
    if session.scalar(select(FundMovement.id).where(FundMovement.transaction_id == t.id)) or session.scalar(
        select(LoanPayment.id).where(LoanPayment.transaction_id == t.id)
    ):
        raise Conflict("Transaction is linked to a fund or loan ledger and cannot be reclassified")
    for model, value in (
        (Category, correction.category_id),
        (Project, correction.project_id),
        (IncomeSource, correction.income_source_id),
    ):
        if value and not session.get(model, value):
            raise ValueError("Unknown category, project or income source")
    if correction.kind in {"purchase", "interest"} and t.amount_minor > 0:
        raise ValueError("A positive amount needs an income/refund/movement purpose")
    if (
        correction.kind in {"income", "refund", "reimbursement", "loan_received", "investment_sale"}
        and t.amount_minor < 0
    ):
        raise ValueError("This purpose requires a positive amount")
    if (
        correction.kind in {"card_settlement", "loan_payment", "mortgage", "cash_withdrawal"}
        and t.amount_minor >= 0
    ):
        raise ValueError("This purpose requires a debit")
    if correction.scope == "project" and not correction.project_id:
        raise ValueError("Project scope requires an explicit project")
    if (
        correction.kind in {"purchase", "interest", "mortgage", "loan_payment"}
        and not correction.category_id
        and not correction.splits
    ):
        raise ValueError("An expense needs a category or a complete split")
    if correction.splits:
        if correction.kind not in {"purchase", "refund", "reimbursement", "interest"}:
            raise ValueError("Only consumption entries may be split")
        if correction.create_merchant_rule:
            raise ValueError("Split exceptions cannot become a merchant-wide rule")
        values = [minor(s.amount) for s in correction.splits]
        if sum(values) != t.amount_minor or any(v == 0 or (v > 0) != (t.amount_minor > 0) for v in values):
            raise ValueError("Signed splits must have the same direction and sum exactly to the transaction")
        for split in correction.splits:
            if (
                not session.get(Category, split.category_id)
                or (split.project_id and not session.get(Project, split.project_id))
                or (split.scope == "project" and not split.project_id)
            ):
                raise ValueError("Invalid split category or project")
    # A community project pays the household back: its expenses are receivables, never household spending.
    project = session.get(Project, correction.project_id) if correction.project_id else None
    community = bool(project and project.reimbursable)
    previous = session.get(Project, t.project_id) if t.project_id else None
    expected = correction.reimbursement_expected
    if community:
        expected = True
    elif expected is None and previous and previous.reimbursable:
        expected = False
    if expected and (t.amount_minor >= 0 or correction.kind != "purchase" or correction.splits):
        raise ValueError(
            "Community project expenses and expected reimbursements must be unsplit purchase debits"
            if community
            else "Expected reimbursement requires an unsplit purchase debit"
        )
    before = snapshot(session, t)
    reimbursement = session.scalar(select(Reimbursement).where(Reimbursement.expense_id == t.id))
    if reimbursement and expected is not False and (correction.kind != "purchase" or correction.splits):
        raise ValueError(
            "Remove the expected reimbursement explicitly before changing its purpose or splitting it"
        )
    if expected is not None:
        receivables.sync_for_transaction(session, t, expected, correction.note)
    for key in ("category_id", "scope", "kind", "project_id", "income_source_id"):
        setattr(t, key, getattr(correction, key))
    if community:
        t.scope = "project"
    if correction.merchant is not None:
        t.merchant = correction.merchant
    if correction.allocation_date:
        if correction.allocation_date != t.allocation_date:
            t.allocation_manual = True
        t.allocation_date = correction.allocation_date
    session.execute(delete(TransactionSplit).where(TransactionSplit.transaction_id == t.id))
    for split in correction.splits:
        session.add(
            TransactionSplit(
                transaction_id=t.id,
                category_id=split.category_id,
                amount_minor=minor(split.amount),
                scope=split.scope,
                project_id=split.project_id,
            )
        )
    t.manual, t.review_status, t.classification_confidence, t.classification_provenance = (
        True,
        "confirmed",
        100,
        "manual",
    )
    t.version += 1
    item = session.scalar(select(ReviewItem).where(ReviewItem.transaction_id == t.id))
    if item:
        item.status = "resolved"
    session.flush()
    after = snapshot(session, t)
    session.add(
        ClassificationDecision(transaction_id=t.id, result=after, confidence=100, provenance="manual")
    )
    session.add(
        AuditEvent(
            entity_type="transaction",
            entity_id=t.id,
            action="correct",
            before=before,
            after={**after, "note": correction.note},
        )
    )
    if correction.create_merchant_rule:
        category = session.get(Category, t.category_id) if t.category_id else None
        conditions = {
            "merchant_in": [normalized(t.merchant)],
            "direction": "credit" if t.amount_minor > 0 else "debit",
            "currency": t.currency,
        }
        result = {"scope": t.scope, "kind": t.kind}
        if category:
            result["category_code"] = category.code
        if t.project_id:
            raise ValueError("Project-specific corrections cannot become global rules")
        session.add(
            ClassificationRule(
                name=f"User rule: {t.merchant}",
                priority=15,
                conditions=conditions,
                result=result,
                confirmed=True,
            )
        )
    return t
