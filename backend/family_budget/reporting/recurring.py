"""Fixed expenses in a period: which already arrived, which are still to come, and what that leaves.

A fixed expense arrived when a transaction under its merchant counts in the period (the same date rule as
the budget, so a card subscription belongs to the bill it is charged on). Unreviewed transactions count as
arrived too: the money left even if nobody classified it yet. Matching is by name only; the bank and the
card sites truncate names differently, so either name may be a prefix of the other.
"""

from collections import defaultdict
from datetime import date, timedelta
from statistics import median

from sqlalchemy import select

from ..models import Category, RecurringExpense, Transaction
from ..normalization.money import money

SPENDING = {"purchase", "interest", "mortgage", "loan_payment"}
RESERVE = {"fund", "savings"}


def name_key(text):
    return " ".join((text or "").split()).casefold()


def same_merchant(expected, actual):
    a, b = name_key(expected), name_key(actual)
    return bool(a) and len(min(a, b, key=len)) >= 4 and (a.startswith(b) or b.startswith(a))


def due_date(period_start, month_end, day):
    """The day in [period_start, month_end) with this day of month (periods are at most 31 days)."""
    year, month = period_start.year, period_start.month
    if day < period_start.day:
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return min(date(year, month, day), month_end - timedelta(days=1))


def in_period(session, period_start, month_end, counted_on, categories, allocations, remaining_budget_minor):
    period_rows = [
        t
        for t in session.scalars(
            select(Transaction).where(
                Transaction.amount_minor < 0,
                Transaction.currency == "ILS",
                Transaction.kind.in_(SPENDING),
            )
        )
        if period_start <= counted_on(t) < month_end
    ]
    items, upcoming, unreviewed = [], 0, 0
    expected_by_category = defaultdict(int)
    for r in session.scalars(select(RecurringExpense).where(RecurringExpense.active.is_(True))):
        category = categories.get(r.category_id)
        reserve = bool(category and category.kind in RESERVE)
        expected_by_category[r.category_id] += r.expected_minor
        matches = [t for t in period_rows if r.merchant and same_merchant(r.merchant, t.merchant)]
        paid = -sum(t.amount_minor for t in matches)
        status = "arrived" if matches else "upcoming"
        if matches and any(t.review_status != "confirmed" for t in matches):
            status = "unreviewed"
        if not reserve:
            if status == "upcoming":
                upcoming += r.expected_minor
            elif status == "unreviewed":  # left the account but not yet subtracted from the budget
                unreviewed += -sum(t.amount_minor for t in matches if t.review_status != "confirmed")
        items.append(
            {
                "id": r.id,
                "name": r.name,
                "merchant": r.merchant,
                "category_id": r.category_id,
                "category": category.name if category else None,
                "reserve": reserve,
                "expected": money(r.expected_minor),
                "due": due_date(period_start, month_end, r.day_of_month).isoformat(),
                "status": status,
                "paid": money(paid) if matches else None,
                # When it happened and, for a card, the bill that charges it; never the internal budget date.
                "charges": sorted(
                    {
                        (t.transaction_date.isoformat(), t.posting_date.isoformat() if t.card_id else None)
                        for t in matches
                    }
                ),
            }
        )
    items.sort(key=lambda i: (i["status"] != "upcoming", i["due"], i["name"]))
    planned = {a.category_id: a.amount_minor for a in allocations}
    short = [
        {
            "category_id": cid,
            "name": categories[cid].name,
            "fixed": money(total),
            "planned": money(planned.get(cid, 0)),
        }
        for cid, total in expected_by_category.items()
        if cid in categories and total > planned.get(cid, 0)
    ]
    return {
        "items": items,
        "upcoming": money(upcoming),
        "unreviewed": money(unreviewed),
        "free_to_spend": money(remaining_budget_minor - upcoming - unreviewed),
        "configured": len(items),
        "unmatched": sum(1 for r in items if not r["merchant"]),
        "short_categories": sorted(short, key=lambda s: s["name"]),
    }


def suggestions(session, today=None, months=4):
    """Outgoing charges seen in at least two calendar months of the last few, at a steady amount, that no
    fixed expense covers yet. Supermarkets and other shops visited many times a month are left out."""
    today = today or date.today()
    since = (today.replace(day=1) - timedelta(days=31 * (months - 1))).replace(day=1)
    known = [r.merchant for r in session.scalars(select(RecurringExpense)) if r.merchant]
    groups = defaultdict(lambda: defaultdict(list))
    names = {}
    for t in session.scalars(
        select(Transaction)
        .where(
            Transaction.amount_minor < 0,
            Transaction.currency == "ILS",
            Transaction.kind.in_(SPENDING),
            Transaction.transaction_date >= since,
        )
        .order_by(Transaction.transaction_date)
    ):
        key = name_key(t.merchant)
        groups[key][t.transaction_date.strftime("%Y-%m")].append(t)
        names[key] = t.merchant
    categories = {c.id: c for c in session.scalars(select(Category))}
    found = []
    for key, by_month in groups.items():
        if any(same_merchant(k, key) for k in known):
            continue
        totals = {m: -sum(t.amount_minor for t in rows) for m, rows in by_month.items() if len(rows) <= 2}
        if len(totals) < 2 or len(by_month) - len(totals) > 0:
            continue
        middle = median(totals.values())
        steady = [m for m, v in totals.items() if abs(v - middle) <= 0.2 * middle]
        if len(steady) < 2:
            continue
        latest_month = max(steady)
        latest = by_month[latest_month][-1]
        category = next(
            (
                t.category_id
                for m in sorted(by_month, reverse=True)
                for t in reversed(by_month[m])
                if t.category_id
            ),
            None,
        )
        found.append(
            {
                "merchant": names[key],
                "expected": money(totals[latest_month]),
                "day_of_month": min(latest.transaction_date.day, 28),
                "category_id": category,
                "category": categories[category].name if category in categories else None,
                "months": sorted(totals),
                "source": "bank" if latest.card_id is None else "card",
            }
        )
    return sorted(found, key=lambda s: -float(s["expected"]))
