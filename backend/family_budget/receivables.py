"""Money community projects owe the household: expenses paid for a reimbursable project, the payments that
came back (bank, Bit, PayBox or cash, possibly one payment for several receipts) and what is still owed."""

from collections import defaultdict
from datetime import date

from sqlalchemy import func, select

from .models import (
    Attachment,
    AuditEvent,
    Project,
    Reimbursement,
    ReimbursementAllocation,
    ReimbursementPayment,
    Transaction,
)
from .normalization.money import minor, money

METHODS = ("bank", "bit", "paybox", "cash", "other")


def received(session, reimbursement, before=None):
    """Amount already paid back for one expense, optionally only by payments received before a date."""
    query = (
        select(func.coalesce(func.sum(ReimbursementAllocation.amount_minor), 0))
        .join(ReimbursementPayment, ReimbursementPayment.id == ReimbursementAllocation.payment_id)
        .where(ReimbursementAllocation.reimbursement_id == reimbursement.id)
    )
    if before:
        query = query.where(ReimbursementPayment.received_on < before)
    paid = session.scalar(query)
    # The original one-to-one reconciliation (a matched credit) settles the whole expense.
    return reimbursement.expected_minor if reimbursement.refund_id else paid


def outstanding(session, reimbursement, before=None):
    return reimbursement.expected_minor - received(session, reimbursement, before)


def sync_for_transaction(session, t, wanted, note):
    """Create or remove the receivable of one expense. Removal is refused once money was received."""
    record = session.scalar(select(Reimbursement).where(Reimbursement.expense_id == t.id))
    if wanted and not record:
        session.add(Reimbursement(expense_id=t.id, expected_minor=-t.amount_minor, note=note))
    elif not wanted and record:
        if record.refund_id or session.scalar(
            select(ReimbursementAllocation.id).where(ReimbursementAllocation.reimbursement_id == record.id)
        ):
            raise ValueError("This expense was already (partly) reimbursed; undo the reimbursement first")
        session.delete(record)


def open_items(session, project_id):
    rows = session.execute(
        select(Reimbursement, Transaction)
        .join(Transaction, Transaction.id == Reimbursement.expense_id)
        .where(Transaction.project_id == project_id)
        .order_by(Transaction.transaction_date, Transaction.id)
    ).all()
    return [(r, t, outstanding(session, r)) for r, t in rows]


def record_payment(session, project_id, received_on, amount, method, note, reimbursement_ids, transaction_id):
    project = session.get(Project, project_id)
    if not project or not project.reimbursable:
        raise ValueError("Choose a community project that reimburses expenses")
    if method not in METHODS:
        raise ValueError("Unknown payment method")
    value = minor(amount)
    if value <= 0:
        raise ValueError("Reimbursement amount must be positive")
    if transaction_id:
        credit = session.get(Transaction, transaction_id)
        if not credit or credit.amount_minor != value:
            raise ValueError("The linked bank transaction must be a credit of exactly this amount")
    items = [(r, t, due) for r, t, due in open_items(session, project_id) if due > 0]
    if reimbursement_ids is not None:
        chosen = set(reimbursement_ids)
        unknown = chosen - {r.id for r, _, _ in items}
        if unknown:
            raise ValueError("Selected expenses are not open receivables of this project")
        items = [item for item in items if item[0].id in chosen]
    if value > sum(due for _, _, due in items):
        raise ValueError("The amount is more than what this project still owes for the selected expenses")
    payment = ReimbursementPayment(
        project_id=project_id,
        received_on=received_on,
        amount_minor=value,
        method=method,
        note=note,
        transaction_id=transaction_id,
    )
    session.add(payment)
    session.flush()
    left, allocations = value, []
    for record, _, due in items:  # oldest receipts first
        if left == 0:
            break
        part = min(due, left)
        session.add(
            ReimbursementAllocation(payment_id=payment.id, reimbursement_id=record.id, amount_minor=part)
        )
        allocations.append({"reimbursement_id": record.id, "amount_minor": part})
        left -= part
        if part == due:
            record.status = "reimbursed"
    session.add(
        AuditEvent(
            entity_type="reimbursement_payments",
            entity_id=payment.id,
            action="record",
            before={},
            after={
                "project_id": project_id,
                "received_on": received_on.isoformat(),
                "amount_minor": value,
                "method": method,
                "allocations": allocations,
            },
        )
    )
    return payment


def delete_payment(session, payment_id):
    payment = session.get(ReimbursementPayment, payment_id)
    if not payment:
        raise ValueError("Unknown reimbursement payment")
    allocations = list(
        session.scalars(
            select(ReimbursementAllocation).where(ReimbursementAllocation.payment_id == payment_id)
        )
    )
    for allocation in allocations:
        session.get(Reimbursement, allocation.reimbursement_id).status = "pending"
        session.delete(allocation)
    session.add(
        AuditEvent(
            entity_type="reimbursement_payments",
            entity_id=payment.id,
            action="delete",
            before={
                "project_id": payment.project_id,
                "amount_minor": payment.amount_minor,
                "method": payment.method,
                "allocations": {a.reimbursement_id: a.amount_minor for a in allocations},
            },
            after={},
        )
    )
    session.delete(payment)


def summary(session, today=None):
    today = today or date.today()
    attachments = defaultdict(int)
    for transaction_id in session.scalars(select(Attachment.transaction_id)):
        attachments[transaction_id] += 1
    result = []
    for project in session.scalars(
        select(Project).where(Project.reimbursable.is_(True)).order_by(Project.name)
    ):
        items = open_items(session, project.id)
        due = [(r, t, left) for r, t, left in items if left > 0]
        payments = list(
            session.scalars(
                select(ReimbursementPayment)
                .where(ReimbursementPayment.project_id == project.id)
                .order_by(ReimbursementPayment.received_on.desc())
            )
        )
        result.append(
            {
                "project_id": project.id,
                "name": project.name,
                "paid": money(sum(r.expected_minor for r, _, _ in items)),
                "received": money(sum(r.expected_minor - left for r, _, left in items)),
                "outstanding": money(sum(left for _, _, left in due)),
                "oldest_days": (today - min(t.transaction_date for _, t, _ in due)).days if due else None,
                "items": [
                    {
                        "reimbursement_id": r.id,
                        "transaction_id": t.id,
                        "date": t.transaction_date.isoformat(),
                        "merchant": t.merchant,
                        "expected": money(r.expected_minor),
                        "outstanding": money(left),
                        "attachments": attachments[t.id],
                    }
                    for r, t, left in items
                ],
                "payments": [
                    {
                        "id": p.id,
                        "received_on": p.received_on.isoformat(),
                        "amount": money(p.amount_minor),
                        "method": p.method,
                        "note": p.note,
                    }
                    for p in payments
                ],
            }
        )
    return result


def set_project_reimbursable(session, project, value):
    """Turning a project into a community project makes its purchases receivables; turning it back is
    refused once any money came back."""
    if project.reimbursable == value:
        return
    expenses = list(
        session.scalars(
            select(Transaction).where(
                Transaction.project_id == project.id,
                Transaction.kind == "purchase",
                Transaction.amount_minor < 0,
            )
        )
    )
    for t in expenses:
        sync_for_transaction(session, t, value, "פרויקט קהילתי" if value else "")
        if value:
            t.scope = "project"
    project.reimbursable = value
