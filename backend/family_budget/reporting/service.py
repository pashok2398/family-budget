from collections import defaultdict
from datetime import timedelta
from sqlalchemy import and_, case, func, select
from ..models import (
    Transaction,
    TransactionSplit,
    Category,
    BudgetAllocation,
    SinkingFund,
    FundMovement,
    LoanPayment,
    Loan,
    Reimbursement,
    Project,
)
from .. import periods, receivables
from . import recurring as fixed
from ..budgeting.service import budget_for, validate_month
from ..normalization.money import money

CONSUMPTION = {"purchase", "refund", "interest"}
DEBT = {"mortgage", "loan_payment"}
SAVINGS = {"savings_transfer", "investment_transfer"}


month_shift = periods.month_shift


def budget_date_sql(basis):
    """SQL twin of periods.budget_date."""
    if basis != "charge":
        return Transaction.allocation_date
    return case(
        (
            and_(Transaction.card_id.is_not(None), Transaction.allocation_manual.is_(False)),
            func.date(Transaction.posting_date, "-1 day"),
        ),
        else_=Transaction.allocation_date,
    )


def tx_query(
    month=None,
    account_id=None,
    card_id=None,
    category_id=None,
    project_id=None,
    scope=None,
    review_status=None,
    start_day=periods.DEFAULT_START_DAY,
    basis=periods.DEFAULT_CARD_BASIS,
):
    query = select(Transaction)
    if month:
        first, following = periods.bounds(month, start_day)
        counted = budget_date_sql(basis)
        query = query.where(counted >= first, counted < following)
    for key, value in {
        "account_id": account_id,
        "card_id": card_id,
        "category_id": category_id,
        "project_id": project_id,
        "scope": scope,
        "review_status": review_status,
    }.items():
        if value:
            if key in {"category_id", "project_id", "scope"}:
                split_ids = select(TransactionSplit.transaction_id).where(
                    getattr(TransactionSplit, key) == value
                )
                query = query.where((getattr(Transaction, key) == value) | Transaction.id.in_(split_ids))
            else:
                query = query.where(getattr(Transaction, key) == value)
    return query


SCOPE_LABELS = {"pub": "פאב קהילתי", "investment": "השקעות", "project": "פרויקט"}


def charges(session, period_start, month_end, categories, split_map):
    """What leaves the bank in the period, reviewed or not: card charges by the bill they are on (a charge on
    the period start day closes the previous period) and direct bank spending by posting date, split by
    charge date and by purpose. Card settlements and transfers are excluded, so nothing is counted twice."""
    projects = {p.id: p for p in session.scalars(select(Project))}
    receivable = set(session.scalars(select(Reimbursement.expense_id)))
    by_charge, by_purpose = {}, {}
    total = foreign = 0
    for t in session.scalars(select(Transaction).where(Transaction.kind.in_(CONSUMPTION))):
        counted = t.posting_date - timedelta(days=1) if t.card_id else t.posting_date
        if not period_start <= counted < month_end:
            continue
        if t.currency != "ILS":
            foreign += 1
            continue
        total -= t.amount_minor
        key = (t.posting_date.isoformat(), "card" if t.card_id else "bank")
        charge = by_charge.setdefault(
            key, {"date": key[0], "source": key[1], "total": 0, "count": 0, "cards": set()}
        )
        charge["total"] -= t.amount_minor
        charge["count"] += 1
        if t.card_id:
            charge["cards"].add(t.card_id)
        for portion in split_map.get(t.id) or [t]:
            if t.review_status != "confirmed":
                group = ("pending", "pending", "ממתין לאישור")
            elif t.id in receivable:  # paid for others: leaves the account now, comes back later
                owner = projects.get(t.project_id)
                group = (
                    "receivable:" + (t.project_id or ""),
                    "receivable",
                    "לגבייה: " + (owner.name if owner else "החזר צפוי"),
                )
            elif portion.project_id:
                group = (
                    "project:" + portion.project_id,
                    "project",
                    (projects[portion.project_id].name if portion.project_id in projects else "פרויקט"),
                )
            elif portion.scope != "household":
                group = ("scope:" + portion.scope, "scope", SCOPE_LABELS.get(portion.scope, portion.scope))
            elif portion.category_id and categories[portion.category_id].kind == "fund":
                group = ("fund:" + portion.category_id, "fund", categories[portion.category_id].name)
            else:
                group = ("routine", "routine", "הוצאות שוטפות")
            item = by_purpose.setdefault(
                group[0], {"key": group[0], "type": group[1], "label": group[2], "total": 0, "count": 0}
            )
            item["total"] -= portion.amount_minor
            item["count"] += 1
    card_charges = [c for c in by_charge.values() if c["source"] == "card"]
    # The monthly bill is the charge most cards share; off-cycle charges usually hit one card.
    bill = max(card_charges, key=lambda c: (len(c["cards"]), c["count"]), default=None)
    order = {"routine": 0, "fund": 1, "project": 2, "scope": 3, "receivable": 4, "pending": 5}
    return {
        "total": money(total),
        "foreign_currency_excluded": foreign,
        "by_charge": [
            {
                **c,
                "kind": "bill" if c is bill else "separate" if c["source"] == "card" else "bank",
                "total": money(c["total"]),
                "cards": len(c["cards"]),
            }
            for c in sorted(by_charge.values(), key=lambda c: c["date"])
        ],
        "by_purpose": [
            {**p, "total": money(p["total"])}
            for p in sorted(by_purpose.values(), key=lambda p: (order[p["type"]], p["label"]))
        ],
    }


def report(session, month, **filters):
    validate_month(month)
    period_start, month_end = periods.bounds(month, periods.start_day(session))
    basis = periods.card_basis(session)
    counted_on = lambda t: periods.budget_date(t, basis)  # noqa: E731
    # Query all dates, since cash and consumption deliberately use different date axes.
    transactions = list(session.scalars(tx_query(**filters)))
    categories = {c.id: c for c in session.scalars(select(Category))}
    reimbursements = list(session.scalars(select(Reimbursement)))
    reimbursed_ids = {r.expense_id for r in reimbursements} | {
        r.refund_id for r in reimbursements if r.refund_id
    }
    split_map = defaultdict(list)
    for split in session.scalars(select(TransactionSplit)):
        split_map[split.transaction_id].append(split)
    budget = budget_for(session, month)
    allocations = list(
        session.scalars(select(BudgetAllocation).where(BudgetAllocation.budget_id == budget.id))
    )
    spent, debt_by_category, income, debt, saving, cash, exceptional = (
        defaultdict(int),
        defaultdict(int),
        0,
        0,
        0,
        0,
        0,
    )
    excluded_foreign = pending = 0
    rows_this_month = []
    for t in transactions:
        in_consumption = period_start <= counted_on(t) < month_end
        in_cash = period_start <= t.posting_date < month_end
        if not in_consumption and not in_cash:
            continue
        if t.currency != "ILS":
            excluded_foreign += 1
            continue
        if in_cash and not t.card_id:
            parts = split_map.get(t.id)
            cash += (
                sum(
                    p.amount_minor
                    for p in parts
                    if all(
                        not filters.get(k) or getattr(p, k) == filters[k]
                        for k in ("scope", "category_id", "project_id")
                    )
                )
                if parts
                else t.amount_minor
            )
        if in_cash and t.kind == "income" and t.review_status == "confirmed" and t.scope == "household":
            income += t.amount_minor
        if not in_consumption:
            continue
        rows_this_month.append(t)
        if t.review_status != "confirmed":
            pending += 1
            continue
        if t.scope == "household" and t.kind in DEBT:
            debt -= t.amount_minor
            debt_by_category[t.category_id] -= t.amount_minor
        if t.scope == "household" and t.kind in SAVINGS:
            saving -= t.amount_minor
        if t.kind not in CONSUMPTION or t.id in reimbursed_ids:
            continue
        portions = split_map.get(t.id) or [t]
        for portion in portions:
            if any(
                filters.get(k) and getattr(portion, k) != filters[k]
                for k in ("scope", "category_id", "project_id")
            ):
                continue
            if portion.scope == "household":
                spent[portion.category_id] -= portion.amount_minor
            else:
                exceptional -= portion.amount_minor
    consumption = sum(spent.values())
    funds = []
    for f in session.scalars(select(SinkingFund)):
        balance = session.scalar(
            select(func.coalesce(func.sum(FundMovement.amount_minor), 0)).where(
                FundMovement.fund_id == f.id, FundMovement.date < month_end
            )
        )
        funds.append(
            {
                "id": f.id,
                "name": f.name,
                "category_id": f.category_id,
                "balance": money(balance),
                # The month's plan is the one place a fund's monthly amount is set.
                "monthly_target": money(
                    next((a.amount_minor for a in allocations if a.category_id == f.category_id), 0)
                ),
            }
        )
    category_rows = []
    for a in allocations:
        c = categories[a.category_id]
        actual = spent.get(c.id, 0) + debt_by_category.get(c.id, 0)
        if c.kind == "savings":
            actual = saving
        category_rows.append(
            {
                "id": c.id,
                "name": c.name,
                "kind": c.kind,
                "budget": money(a.amount_minor),
                "actual": money(actual),
                "remaining": money(a.amount_minor - actual),
            }
        )
    allocated_ids = {a.category_id for a in allocations}
    for category_id, amount in spent.items():
        if category_id not in allocated_ids:
            category_rows.append(
                {
                    "id": category_id,
                    "name": categories[category_id].name if category_id else "ללא קטגוריה",
                    "kind": "expense",
                    "budget": "0.00",
                    "actual": money(amount),
                    "remaining": money(-amount),
                }
            )
    # Reserve planned savings/fund allocations; spending from funds does not consume ordinary envelope twice.
    ordinary_plan = sum(
        a.amount_minor for a in allocations if categories[a.category_id].kind not in {"fund", "savings"}
    )
    ordinary_spending = (
        sum(value for cat, value in spent.items() if not cat or categories[cat].kind != "fund") + debt
    )
    latest = {}
    for t in session.scalars(
        select(Transaction)
        .where(Transaction.purchase_id.is_not(None))
        .order_by(Transaction.installment_number, Transaction.posting_date)
    ):
        if counted_on(t) < month_end:
            latest[(t.source_key, t.purchase_id)] = t
    commitments = [
        {
            "transaction_id": t.id,
            "merchant": t.merchant,
            "purchase_id": t.purchase_id,
            "monthly_charge": money(abs(t.amount_minor)),
            "remaining": money(t.remaining_commitment_minor),
            "remaining_installments": t.installment_count - t.installment_number,
            "currency": t.currency,
        }
        for t in latest.values()
        if t.remaining_commitment_minor
    ]
    installments_unknown = sum(1 for t in transactions if t.card_id and t.purchase_id is None)
    pending_reimbursements = 0
    for reimbursement in reimbursements:
        expense = session.get(Transaction, reimbursement.expense_id)
        refund = session.get(Transaction, reimbursement.refund_id) if reimbursement.refund_id else None
        if (
            expense.currency == "ILS"
            and expense.review_status == "confirmed"
            and counted_on(expense) < month_end
            and (refund is None or refund.posting_date >= month_end)
        ):
            # Still owed at the end of the period: expected minus payments received before it.
            pending_reimbursements += (
                receivables.outstanding(session, reimbursement, before=month_end)
                if not refund
                else reimbursement.expected_minor
            )
    projects = []
    for project in session.scalars(select(Project)):
        total = 0
        for t in session.scalars(
            select(Transaction).where(
                Transaction.review_status == "confirmed",
                Transaction.currency == "ILS",
                budget_date_sql(basis) < month_end,
            )
        ):
            if t.kind not in CONSUMPTION or t.id in reimbursed_ids:
                continue
            total -= sum(p.amount_minor for p in (split_map.get(t.id) or [t]) if p.project_id == project.id)
        projects.append(
            {
                "id": project.id,
                "name": project.name,
                "budget": money(project.budget_minor),
                "spent": money(total),
                "remaining": money(project.budget_minor - total),
            }
        )
    loans = []
    for loan in session.scalars(select(Loan)):
        paid = session.scalar(
            select(func.coalesce(func.sum(LoanPayment.principal_minor), 0))
            .join(Transaction, Transaction.id == LoanPayment.transaction_id)
            .where(LoanPayment.loan_id == loan.id, budget_date_sql(basis) < month_end)
        )
        loans.append(
            {
                "id": loan.id,
                "name": loan.name,
                "monthly_payment": money(loan.monthly_payment_minor),
                "remaining_principal": money(loan.principal_minor - paid)
                if loan.principal_minor is not None
                else None,
            }
        )
    return {
        "pending_reimbursements": money(pending_reimbursements),
        "projects": projects,
        "month": month,
        "period_start": period_start.isoformat(),
        "period_end": (month_end - timedelta(days=1)).isoformat(),
        "card_spending_basis": basis,
        "charges": charges(session, period_start, month_end, categories, split_map),
        "income": money(income),
        "consumption": money(consumption),
        "debt_payments": money(debt),
        "savings_contributions": money(saving),
        "net_cash_flow": money(cash),
        "exceptional": money(exceptional),
        "planned_income": money(budget.income_minor),
        "planned_total": money(sum(a.amount_minor for a in allocations)),
        "remaining_budget": money(ordinary_plan - ordinary_spending),
        "fixed_expenses": fixed.in_period(
            session,
            period_start,
            month_end,
            counted_on,
            categories,
            allocations,
            ordinary_plan - ordinary_spending,
        ),
        "provisional_income": budget.provisional,
        "categories": category_rows,
        "funds": funds,
        "commitments": commitments,
        "loans": loans,
        "pending_review": pending,
        "foreign_currency_excluded": excluded_foreign,
        "transaction_count": len(rows_this_month),
        "unverified_installment_records": installments_unknown,
        "cash_balance_verified": False,
        "filtered_budget_comparison": bool(any(filters.values())),
        "note": "Remaining budget is a plan variance, never verified cash savings. Pending classifications are excluded from consumption; bank cash flow includes all imported bank movements.",
    }


def trends(session, month):
    return [
        {k: r[k] for k in ("month", "income", "consumption", "net_cash_flow")}
        for r in (report(session, month_shift(month, d)) for d in range(-5, 1))
    ]


def adjustments(session, month):
    months = [month_shift(month, d) for d in (-3, -2, -1)]
    reports = [report(session, m) for m in months]
    if any(
        r["transaction_count"] == 0 or r["pending_review"] or r["foreign_currency_excluded"] for r in reports
    ):
        return {
            "eligible": False,
            "reason": "Three prior months of reviewed ILS history required; statement completeness still needs user verification",
            "suggestions": [],
        }
    totals = defaultdict(int)
    from ..normalization.money import minor

    for r in reports:
        for c in r["categories"]:
            if c["kind"] == "expense":
                totals[c["id"]] += minor(c["actual"])
    current = report(session, month)
    suggestions = []
    for c in current["categories"]:
        average = (totals[c["id"]] + 1) // 3
        if c["kind"] == "expense" and average > minor(c["budget"]):
            suggestions.append(
                {
                    "category_id": c["id"],
                    "name": c["name"],
                    "current": c["budget"],
                    "suggested": money(average),
                    "reason": "Average across three previous reviewed months; requires explicit budget edit",
                }
            )
    return {
        "eligible": True,
        "months": months,
        "suggestions": suggestions,
        "history_completeness_verified": False,
    }
