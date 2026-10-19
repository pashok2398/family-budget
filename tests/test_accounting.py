from datetime import date
from uuid import uuid4
import pytest

pytest.importorskip("sqlalchemy")
from sqlalchemy import select
from family_budget.db import session_scope
from family_budget.models import (
    ReviewItem,
    Transaction,
    SourceDocument,
    Account,
    Category,
    AuditEvent,
    ClassificationDecision,
    SinkingFund,
    BudgetAllocation,
)
from family_budget.reporting.service import report
from family_budget.reconciliation.engine import reconcile, undo, Conflict
from family_budget.classification.corrections import correct
from family_budget.classification.engine import classify
from family_budget.api.schemas import Correction
from family_budget.budgeting.service import move_fund


def tx(
    session,
    account,
    amount,
    *,
    card=None,
    merchant="Test",
    kind="purchase",
    category="groceries",
    scope="household",
    when="2026-10-05",
    posting="2026-10-10",
    confirmed=True,
    **extra,
):
    doc = SourceDocument(sha256=uuid4().hex * 2, extension=".csv", size=1)
    session.add(doc)
    session.flush()
    category_id = session.scalar(select(Category.id).where(Category.code == category)) if category else None
    item = Transaction(
        account_id=account,
        card_id=card,
        document_id=doc.id,
        source_row=2,
        source_key=f"card:{card}" if card else f"account:{account}",
        fingerprint=uuid4().hex,
        base_fingerprint=uuid4().hex,
        transaction_date=date.fromisoformat(when),
        posting_date=date.fromisoformat(posting),
        allocation_date=date.fromisoformat(when),
        merchant=merchant,
        raw_description=merchant,
        amount_minor=amount,
        original_amount_minor=amount,
        currency="ILS",
        original_currency="ILS",
        kind=kind,
        scope=scope,
        category_id=category_id,
        review_status="confirmed" if confirmed else "pending",
        **extra,
    )
    session.add(item)
    session.flush()
    return item


def test_cash_consumption_and_reversible_settlement(ledger):
    engine, root, account, card = ledger
    with session_scope(engine, True) as s:
        a = tx(s, account, -40000, card=card)
        b = tx(s, account, -20000, card=card)
        settlement = tx(s, account, -60000, kind="card_settlement")
        tx(s, account, 3200000, kind="income", category="income")
        tx(s, account, -110000, kind="loan_payment", category="loan")
        tx(s, account, -150000, kind="savings_transfer", category="savings")
        match = reconcile(s, "card_settlement", [settlement.id, a.id, b.id], "Exact statement billing match")
        totals = report(s, "2026-10")
        assert totals["consumption"] == "600.00"
        assert totals["debt_payments"] == "1100.00"
        assert totals["savings_contributions"] == "1500.00"
        assert totals["net_cash_flow"] == "28800.00"
        assert totals["income"] == "32000.00"
        undo(s, match.id)
        assert a.reconciliation_status == "unmatched"
        assert report(s, "2026-10")["consumption"] == "600.00"


def test_own_transfer_and_savings_not_consumption(ledger):
    engine, root, account, card = ledger
    with session_scope(engine, True) as s:
        second = Account(name="Savings", institution="other")
        s.add(second)
        s.flush()
        debit = tx(s, account, -50000, kind="savings_transfer", category="savings")
        credit = tx(s, second.id, 50000, kind="internal_transfer")
        reconcile(s, "internal_transfer", [debit.id, credit.id], "Confirmed own savings account transfer")
        d = report(s, "2026-10")
        assert d["net_cash_flow"] == "0.00"
        assert d["consumption"] == "0.00"
        assert d["savings_contributions"] == "500.00"


def test_wrong_settlement_never_matches(ledger):
    engine, root, account, card = ledger
    with session_scope(engine, True) as s:
        a = tx(s, account, -40000, card=card)
        b = tx(s, account, -41000, kind="card_settlement")
        with pytest.raises(ValueError, match="total"):
            reconcile(s, "card_settlement", [b.id, a.id], "Bad total must be rejected")
        assert a.reconciliation_status == "unmatched"


def test_posting_date_and_installment_allocation(ledger):
    engine, root, account, card = ledger
    with session_scope(engine, True) as s:
        tx(
            s,
            account,
            -10000,
            card=card,
            when="2026-10-05",
            posting="2026-11-10",
            purchase_id="purchase-1",
            installment_number=1,
            installment_count=3,
            original_purchase_minor=30000,
            remaining_commitment_minor=20000,
        )
        tx(s, account, -10000, kind="card_settlement", when="2026-10-05", posting="2026-11-10")
        assert report(s, "2026-10")["net_cash_flow"] == "0.00"
        assert report(s, "2026-11")["net_cash_flow"] == "-100.00"
        assert report(s, "2026-10")["commitments"][0]["remaining"] == "200.00"


def test_refund_preserves_scope_and_can_undo(ledger):
    engine, root, account, card = ledger
    with session_scope(engine, True) as s:
        expense = tx(s, account, -370000, scope="pub")
        credit = tx(s, account, 370000, kind="reimbursement", category=None, when="2026-10-15")
        match = reconcile(
            s, "reimbursement", [expense.id, credit.id], "Committee paid exact furniture reimbursement"
        )
        assert credit.scope == "pub" and credit.category_id == expense.category_id
        assert report(s, "2026-10")["exceptional"] == "0.00"
        undo(s, match.id)
        assert credit.scope == "household" and credit.category_id is None


def test_manual_corrections_splits_and_history(ledger):
    engine, root, account, card = ledger
    with session_scope(engine, True) as s:
        item = tx(s, account, -10000, card=card, confirmed=False)
        groceries = s.scalar(select(Category.id).where(Category.code == "groceries"))
        hobbies = s.scalar(select(Category.id).where(Category.code == "hobbies"))
        changes = Correction(
            id=item.id,
            version=1,
            category_id=groceries,
            note="Split household and pub",
            splits=[
                {"category_id": groceries, "amount": "-60.00", "scope": "household"},
                {"category_id": hobbies, "amount": "-40.00", "scope": "pub"},
            ],
        )
        correct(s, changes)
        assert report(s, "2026-10")["consumption"] == "60.00"
        assert report(s, "2026-10")["exceptional"] == "40.00"
        with pytest.raises(Conflict):
            correct(s, changes)
        assert (
            s.scalar(select(AuditEvent).where(AuditEvent.entity_id == item.id)).before["review_status"]
            == "pending"
        )
        assert (
            s.scalar(
                select(ClassificationDecision).where(ClassificationDecision.transaction_id == item.id)
            ).provenance
            == "manual"
        )
        classify(s, item)
        assert item.manual


def test_review_all_imports_keeps_rule_category_as_suggestion(ledger):
    from family_budget.periods import set_review_all

    engine, root, account, card = ledger
    with session_scope(engine, True) as s:
        set_review_all(s, True)
        item = tx(s, account, -10000, card=card, merchant="PAYBOX", when="2026-10-13", confirmed=False)
        classify(s, item)
        assert item.scope == "pub" and item.review_status == "pending"
        assert item.classification_provenance.startswith("rule:")
        assert s.scalar(select(ReviewItem).where(ReviewItem.transaction_id == item.id)).status == "open"


def test_date_scoped_paybox_and_history_suggestions(ledger):
    engine, root, account, card = ledger
    with session_scope(engine, True) as s:
        exact = tx(s, account, -10000, card=card, merchant="PAYBOX", when="2026-10-13", confirmed=False)
        classify(s, exact)
        assert exact.scope == "pub" and exact.review_status == "confirmed"
        other = tx(s, account, -10000, card=card, merchant="PAYBOX", when="2026-10-14", confirmed=False)
        classify(s, other)
        assert other.review_status == "pending" and other.scope == "household"
        cat = s.scalar(select(Category.id).where(Category.code == "groceries"))
        correct(s, Correction(id=other.id, version=1, category_id=cat, note="Personal exception"))
        future = tx(s, account, -20000, card=card, merchant="PAYBOX", when="2026-11-01", confirmed=False)
        classify(s, future)
        assert future.classification_provenance == "history_suggestion" and future.review_status == "pending"


def test_fund_balance_carries_without_double_counting(ledger):
    engine, root, account, card = ledger
    with session_scope(engine, True) as s:
        fund = s.scalar(select(SinkingFund).join(Category).where(Category.code == "maintenance"))
        move_fund(s, fund.id, "1000.00", date(2026, 9, 1), "September allocation", "request-sept")
        move_fund(s, fund.id, "1000.00", date(2026, 10, 1), "October allocation", "request-oct")
        expense = tx(s, account, -97500, category="maintenance")
        move_fund(s, fund.id, "-975.00", date(2026, 10, 5), "Repair purchase", "request-purchase", expense.id)
        # Same request is a no-op.
        move_fund(s, fund.id, "-975.00", date(2026, 10, 5), "Repair purchase", "request-purchase", expense.id)
        d = report(s, "2026-10")
        assert next(f for f in d["funds"] if f["id"] == fund.id)["balance"] == "1025.00"
        assert d["consumption"] == "975.00"
        assert d["remaining_budget"] == "28300.00"
        with pytest.raises(ValueError, match="Insufficient"):
            move_fund(s, fund.id, "-2000", date(2026, 10, 5), "Overdraw", "request-overdraw")


def test_seed_budget_exact_total_and_foreign_currency_flag(ledger):
    engine, root, account, card = ledger
    with session_scope(engine, True) as s:
        assert sum(a.amount_minor for a in s.scalars(select(BudgetAllocation))) == 3200000
        t = tx(s, account, -5000)
        t.currency = "USD"
        d = report(s, "2026-10")
        assert d["consumption"] == "0.00" and d["foreign_currency_excluded"] == 1
        assert d["cash_balance_verified"] is False


def test_settled_purchase_can_be_refunded_and_matches_undo_independently(ledger):
    engine, root, account, card = ledger
    with session_scope(engine, True) as s:
        purchase = tx(s, account, -10000, card=card)
        bank = tx(s, account, -10000, kind="card_settlement")
        settlement = reconcile(s, "card_settlement", [bank.id, purchase.id], "Exact bank settlement")
        refund = tx(s, account, 10000, card=card, kind="refund", when="2026-10-20")
        refund_match = reconcile(s, "refund", [purchase.id, refund.id], "Confirmed purchase refund")
        assert report(s, "2026-10")["consumption"] == "0.00"
        undo(s, settlement.id)
        assert purchase.reconciliation_status == "matched"
        assert bank.reconciliation_status == "unmatched"
        undo(s, refund_match.id)
        assert purchase.reconciliation_status == "unmatched"
        assert refund.reconciliation_status == "unmatched"


def test_backdated_fund_withdrawal_cannot_use_future_allocation(ledger):
    engine, root, account, card = ledger
    with session_scope(engine, True) as s:
        fund = s.scalar(select(SinkingFund))
        move_fund(s, fund.id, "100", date(2026, 11, 1), "Future allocation", "future-alloc")
        with pytest.raises(ValueError, match="Insufficient"):
            move_fund(s, fund.id, "-50", date(2026, 10, 1), "Backdated withdrawal", "early-withdrawal")


def test_expected_reimbursement_is_not_household_consumption(ledger):
    engine, root, account, card = ledger
    with session_scope(engine, True) as s:
        item = tx(s, account, -568000, card=card, confirmed=False)
        cat = s.scalar(select(Category.id).where(Category.code == "shopping"))
        correct(
            s,
            Correction(
                id=item.id,
                version=1,
                category_id=cat,
                note="Purchase for others",
                reimbursement_expected=True,
            ),
        )
        d = report(s, "2026-10")
        assert d["consumption"] == "0.00"
        assert d["pending_reimbursements"] == "5680.00"


def test_fixed_expenses_still_to_come_reduce_what_is_free(ledger):
    from family_budget.models import RecurringExpense
    from family_budget.normalization.money import minor
    from family_budget.reporting.recurring import suggestions

    engine, root, account, card = ledger
    with session_scope(engine, True) as s:
        cat = lambda code: s.scalar(select(Category.id).where(Category.code == code))  # noqa: E731
        s.add_all(
            [
                RecurringExpense(
                    name="אינטרנט",
                    category_id=cat("utilities"),
                    expected_minor=12990,
                    day_of_month=3,
                    merchant="ספק אינטרנט",
                ),
                RecurringExpense(
                    name="ביטוח בריאות",
                    category_id=cat("health"),
                    expected_minor=200000,
                    day_of_month=20,
                    merchant="ביטוח בריאות לדוגמה",
                ),
                RecurringExpense(
                    name="ביטוח דירה",
                    category_id=cat("maintenance"),
                    expected_minor=30000,
                    day_of_month=12,
                    merchant="הראל דירה",
                ),
                RecurringExpense(
                    name="ועד",
                    category_id=cat("utilities"),
                    expected_minor=5000,
                    day_of_month=1,
                    merchant="ועד בית",
                ),
            ]
        )
        tx(s, account, -12990, merchant="ספק אינטרנט לדוגמה בע", category="utilities", when="2026-10-03")
        tx(s, account, -5000, merchant="ועד בית", category=None, when="2026-10-01", confirmed=False)
        d = report(s, "2026-10")
        fixed = d["fixed_expenses"]
        status = {i["name"]: i["status"] for i in fixed["items"]}
        assert status == {
            "אינטרנט": "arrived",
            "ביטוח בריאות": "upcoming",
            "ביטוח דירה": "upcoming",
            "ועד": "unreviewed",
        }
        # The fund's fixed expense is paid from the fund, not from the routine budget.
        assert fixed["upcoming"] == "2000.00" and fixed["unreviewed"] == "50.00"
        assert minor(fixed["free_to_spend"]) == minor(d["remaining_budget"]) - 200000 - 5000
        assert [c["name"] for c in fixed["short_categories"]] == ["ביטוח ובריאות"]
        assert next(i for i in fixed["items"] if i["name"] == "ביטוח בריאות")["due"] == "2026-10-20"

        for month in ("2026-08", "2026-09", "2026-10"):
            tx(s, account, -6990, merchant="סלולר חשבון תקופתי", category="utilities", when=month + "-14")
            for day in ("02", "09", "16"):  # a shop visited every week is not a fixed expense
                tx(s, account, -30000, merchant="סופר", when=f"{month}-{day}")
        tx(s, account, -12990, merchant="ספק אינטרנט לדוגמה בע", category="utilities", when="2026-09-03")
        found = {f["merchant"]: f for f in suggestions(s, today=date(2026, 10, 19))}
        assert set(found) == {"סלולר חשבון תקופתי"}  # the internet provider is already a fixed expense
        assert found["סלולר חשבון תקופתי"]["expected"] == "69.90"
        assert found["סלולר חשבון תקופתי"]["day_of_month"] == 14
