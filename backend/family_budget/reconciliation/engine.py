from collections import defaultdict
from sqlalchemy import select
from ..models import (
    Transaction,
    TransactionMatch,
    Reimbursement,
    AuditEvent,
    TransactionSplit,
    MatchMember,
    now,
)


class Conflict(ValueError):
    pass


def role(kind):
    return "refund" if kind in {"refund", "reimbursement"} else kind


def active_roles(session):
    roles = defaultdict(set)
    for transaction_id, kind in session.execute(
        select(MatchMember.transaction_id, TransactionMatch.kind)
        .join(TransactionMatch, TransactionMatch.id == MatchMember.match_id)
        .where(TransactionMatch.active.is_(True))
    ):
        roles[transaction_id].add(role(kind))
    return roles


def eligible(roles, transaction_id, kind):
    existing = roles[transaction_id]
    return not existing or (
        role(kind) not in existing and kind != "internal_transfer" and "internal_transfer" not in existing
    )


def reconcile(session, kind, ids, explanation):
    if kind not in {"card_settlement", "internal_transfer", "refund", "reimbursement"}:
        raise ValueError("Unknown reconciliation kind")
    if len(ids) < 2 or len(set(ids)) != len(ids):
        raise ValueError("Select distinct transactions")
    transactions = [session.get(Transaction, x) for x in ids]
    if any(t is None for t in transactions):
        raise ValueError("Unknown transaction")
    roles = active_roles(session)
    if any(not eligible(roles, t.id, kind) for t in transactions):
        raise Conflict("A transaction already belongs to an incompatible active match")
    if len({t.currency for t in transactions}) != 1:
        raise ValueError("Cannot reconcile different currencies")
    if any(t.review_status != "confirmed" for t in transactions):
        raise ValueError("Confirm classifications before reconciling")
    # Save only fields this match changes. A settled purchase can later be refunded,
    # and either independent relationship can be undone without destroying the other.
    before = {t.id: {} for t in transactions}

    def change(t, field, value):
        if getattr(t, field) != value:
            before[t.id][field] = getattr(t, field)
            setattr(t, field, value)

    if kind == "card_settlement":
        banks = [t for t in transactions if not t.card_id]
        cards = [t for t in transactions if t.card_id]
        if len(banks) != 1 or not cards or len({t.card_id for t in cards}) != 1:
            raise ValueError("Settlement requires one bank debit and charges on one card")
        bank = banks[0]
        if bank.amount_minor >= 0 or sum(t.amount_minor for t in cards) != bank.amount_minor:
            raise ValueError("Card charge total must equal the bank debit")
        if any(t.account_id != bank.account_id or t.posting_date != bank.posting_date for t in cards):
            raise ValueError("Card billing date and settlement account must match exactly")
        if bank.kind != "card_settlement" or any(
            t.kind not in {"purchase", "refund", "reimbursement", "interest"} for t in cards
        ):
            raise ValueError("Confirm bank settlement and card charge purposes first")
    elif kind == "internal_transfer":
        if (
            len(transactions) != 2
            or sum(t.amount_minor for t in transactions) != 0
            or any(t.card_id for t in transactions)
        ):
            raise ValueError("Transfer requires equal opposite bank amounts")
        if transactions[0].account_id == transactions[1].account_id or transactions[0].amount_minor == 0:
            raise ValueError("Transfer must be between different accounts")
        if abs((transactions[0].posting_date - transactions[1].posting_date).days) > 7:
            raise ValueError("Transfer dates are more than seven days apart")
        for t in transactions:
            if t.amount_minor > 0 or t.kind not in {"savings_transfer", "investment_transfer"}:
                change(t, "kind", "internal_transfer")
    else:
        if len(transactions) != 2 or sum(t.amount_minor for t in transactions) != 0:
            raise ValueError("MVP matching requires an exact full refund; partial refunds stay in review")
        expense = next((t for t in transactions if t.amount_minor < 0), None)
        credit = next((t for t in transactions if t.amount_minor > 0), None)
        if not expense or not credit or expense.kind not in {"purchase", "interest"}:
            raise ValueError("Select an expense and a positive refund")
        if kind == "refund" and session.scalar(
            select(Reimbursement.id).where(Reimbursement.expense_id == expense.id)
        ):
            raise ValueError("This expense expects reimbursement; use a reimbursement match")
        if credit.transaction_date < expense.transaction_date:
            raise ValueError("Refund predates the expense")
        if any(
            session.scalar(select(TransactionSplit.id).where(TransactionSplit.transaction_id == t.id))
            for t in transactions
        ):
            raise ValueError("Split refunds require manual allocation; automatic matching is unavailable")
        for field, value in {
            "kind": kind,
            "scope": expense.scope,
            "category_id": expense.category_id,
            "project_id": expense.project_id,
        }.items():
            change(credit, field, value)
    for t in transactions:
        t.reconciliation_status = "matched"
        t.version += 1
    match = TransactionMatch(
        kind=kind,
        members=ids,
        explanation=explanation,
        before=before,
        after_versions={t.id: t.version for t in transactions},
    )
    session.add(match)
    session.flush()
    for transaction_id in ids:
        session.add(MatchMember(match_id=match.id, transaction_id=transaction_id))
    if kind == "reimbursement":
        record = session.scalar(select(Reimbursement).where(Reimbursement.expense_id == expense.id))
        match.before = {**before, "_reimbursement": {"existed": record is not None}}
        if not record:
            record = Reimbursement(
                expense_id=expense.id, expected_minor=abs(expense.amount_minor), note=explanation
            )
            session.add(record)
        record.refund_id, record.match_id, record.status = credit.id, match.id, "matched"
    session.add(
        AuditEvent(
            entity_type="match",
            entity_id=match.id,
            action="reconcile",
            before=before,
            after={"members": ids, "kind": kind, "explanation": explanation},
        )
    )
    return match


def undo(session, match_id):
    match = session.get(TransactionMatch, match_id)
    if not match:
        raise ValueError("Unknown match")
    if not match.active:
        return match
    match.active, match.reversed_at = False, now()
    session.flush()
    roles = active_roles(session)
    for tx_id, state in match.before.items():
        if tx_id.startswith("_"):
            continue
        t = session.get(Transaction, tx_id)
        for field, value in state.items():
            setattr(t, field, value)
        t.reconciliation_status = "matched" if roles[tx_id] else "unmatched"
        t.version += 1
    for record in session.scalars(select(Reimbursement).where(Reimbursement.match_id == match.id)):
        if not match.before.get("_reimbursement", {}).get("existed", True):
            session.delete(record)
        else:
            record.refund_id, record.match_id, record.status = None, None, "pending"
    session.add(
        AuditEvent(
            entity_type="match",
            entity_id=match.id,
            action="undo",
            before={"active": True},
            after={"active": False},
        )
    )
    return match


def suggestions(session):
    rows = list(session.scalars(select(Transaction).where(Transaction.review_status == "confirmed")))
    roles = active_roles(session)
    proposals = []
    reimbursement_expenses = set(session.scalars(select(Reimbursement.expense_id)))
    card_groups = defaultdict(list)
    for t in rows:
        if (
            t.card_id
            and eligible(roles, t.id, "card_settlement")
            and t.kind in {"purchase", "refund", "reimbursement", "interest"}
        ):
            card_groups[(t.account_id, t.posting_date, t.currency, t.card_id)].append(t)
    for bank in rows:
        if (
            bank.card_id
            or bank.kind != "card_settlement"
            or bank.amount_minor >= 0
            or not eligible(roles, bank.id, "card_settlement")
        ):
            continue
        for (account, when, currency, _), group in card_groups.items():
            if (account, when, currency) == (bank.account_id, bank.posting_date, bank.currency) and sum(
                t.amount_minor for t in group
            ) == bank.amount_minor:
                proposals.append(
                    {
                        "kind": "card_settlement",
                        "ids": [bank.id] + [t.id for t in group],
                        "explanation": "Same account, billing date, currency and exact charge total; approval required",
                    }
                )
    positives = defaultdict(list)
    for t in rows:
        if t.amount_minor > 0:
            positives[(t.currency, t.amount_minor)].append(t)
    for expense in [t for t in rows if t.amount_minor < 0]:
        for credit in positives[(expense.currency, -expense.amount_minor)]:
            if len(proposals) >= 200:
                return proposals[:200]
            if (
                not expense.card_id
                and not credit.card_id
                and expense.account_id != credit.account_id
                and abs((expense.posting_date - credit.posting_date).days) <= 7
                and eligible(roles, expense.id, "internal_transfer")
                and eligible(roles, credit.id, "internal_transfer")
            ):
                proposals.append(
                    {
                        "kind": "internal_transfer",
                        "ids": [expense.id, credit.id],
                        "explanation": "Equal opposite amounts between owned accounts within seven days; verify purpose",
                    }
                )
            if (
                expense.kind == "purchase"
                and credit.kind in {"refund", "reimbursement"}
                and expense.transaction_date <= credit.transaction_date
                and eligible(roles, expense.id, credit.kind)
                and eligible(roles, credit.id, credit.kind)
            ):
                proposals.append(
                    {
                        "kind": "reimbursement" if expense.id in reimbursement_expenses else credit.kind,
                        "ids": [expense.id, credit.id],
                        "explanation": "Exact expense/credit amount and currency; verify originating purchase",
                    }
                )
    return proposals[:200]
