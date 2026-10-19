from sqlalchemy import select
from ..models import (
    ClassificationRule,
    ClassificationDecision,
    Transaction,
    ReviewItem,
    Category,
    IncomeSource,
    Project,
    Reimbursement,
)
from ..normalization.money import normalized


from .matching import matches
from ..periods import review_all


def apply_result(session, transaction, result):
    if result.get("category_code"):
        transaction.category_id = session.scalar(
            select(Category.id).where(Category.code == result["category_code"])
        )
    for field in ("scope", "kind"):
        if field in result:
            setattr(transaction, field, result[field])
    if result.get("income_name"):
        transaction.income_source_id = session.scalar(
            select(IncomeSource.id).where(IncomeSource.name == result["income_name"])
        )
    if result.get("project_name"):
        transaction.project_id = session.scalar(
            select(Project.id).where(Project.name == result["project_name"])
        )
    if result.get("reimbursement_expected") and not session.scalar(
        select(Reimbursement.id).where(Reimbursement.expense_id == transaction.id)
    ):
        session.add(
            Reimbursement(
                expense_id=transaction.id,
                expected_minor=abs(transaction.amount_minor),
                note=result.get("note", "Pending exact matching"),
            )
        )


def classify(session, transaction):
    if transaction.manual:
        return
    rules = list(
        session.scalars(
            select(ClassificationRule)
            .where(ClassificationRule.active.is_(True))
            .order_by(ClassificationRule.priority)
        )
    )
    candidates = [r for r in rules if matches(transaction, r.conditions)]
    confirmed_rules = [r for r in candidates if r.confirmed]
    suggested_rules = [r for r in candidates if not r.confirmed]
    previous = list(
        session.scalars(
            select(Transaction).where(
                Transaction.manual.is_(True),
                Transaction.id != transaction.id,
                Transaction.currency == transaction.currency,
            )
        )
    )
    previous = [
        p
        for p in previous
        if normalized(p.merchant) == normalized(transaction.merchant)
        and (p.amount_minor > 0) == (transaction.amount_minor > 0)
    ]
    signatures = {(p.category_id, p.scope, p.kind) for p in previous}
    use_history = not confirmed_rules and len(signatures) == 1
    candidates = confirmed_rules or ([] if use_history else suggested_rules)
    if candidates:
        priority = candidates[0].priority
        top = [r for r in candidates if r.priority == priority]
        result = top[0].result
        conflict = any(r.result != result for r in top)
        confirmed = top[0].confirmed and not conflict and not review_all(session)
        if not conflict:
            apply_result(session, transaction, result)
        transaction.classification_confidence = 100 if top[0].confirmed and not conflict else 65
        transaction.classification_provenance = f"rule:{top[0].id}" if not conflict else "conflicting_rules"
        transaction.review_status = "confirmed" if confirmed else "pending"
        session.add(
            ClassificationDecision(
                transaction_id=transaction.id,
                rule_id=top[0].id,
                result=result if not conflict else {},
                confidence=transaction.classification_confidence,
                provenance=transaction.classification_provenance,
            )
        )
    elif use_history:
        transaction.category_id, transaction.scope, transaction.kind = next(iter(signatures))
        transaction.classification_confidence = 70
        transaction.classification_provenance = "history_suggestion"
        transaction.review_status = "pending"
        session.add(
            ClassificationDecision(
                transaction_id=transaction.id,
                result={
                    "category_id": transaction.category_id,
                    "scope": transaction.scope,
                    "kind": transaction.kind,
                },
                confidence=70,
                provenance="history_suggestion",
            )
        )
    else:
        transaction.review_status = "pending"
        transaction.classification_provenance = (
            "conflicting_history" if len(signatures) > 1 else "unclassified"
        )
    if transaction.review_status == "pending":
        existing = session.scalar(select(ReviewItem).where(ReviewItem.transaction_id == transaction.id))
        if existing:
            existing.reason, existing.status = transaction.classification_provenance, "open"
        else:
            session.add(
                ReviewItem(transaction_id=transaction.id, reason=transaction.classification_provenance)
            )
