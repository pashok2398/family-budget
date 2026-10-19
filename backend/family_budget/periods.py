"""Financial periods. A period is labelled by its starting month ("YYYY-MM") and runs from the
start day of that month up to, but excluding, the same day of the next month. With start day 10,
"2026-10" covers 2026-10-10 through 2026-11-09. Start day 1 gives calendar months."""

from datetime import date, timedelta

from .budgeting.service import validate_month
from .models import AppSetting

START_DAY_KEY = "period_start_day"
DEFAULT_START_DAY = 1
# How card spending is counted: "charge" puts each card charge in the period that ends before it (the bill
# paid on the period start day closes the previous period); "purchase" uses the purchase-based budget date.
CARD_BASIS_KEY = "card_spending_basis"
CARD_BASES = ("charge", "purchase")
DEFAULT_CARD_BASIS = "purchase"


def validate_start_day(day):
    if isinstance(day, bool) or not isinstance(day, int) or not 1 <= day <= 28:
        raise ValueError("Period start day must be between 1 and 28")
    return day


def month_shift(month, delta):
    year, number = map(int, month.split("-"))
    index = year * 12 + number - 1 + delta
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def bounds(month, start_day):
    """(first day, first day of the next period) for a period label."""
    validate_month(month)
    year, number = map(int, month.split("-"))
    following = month_shift(month, 1)
    next_year, next_number = map(int, following.split("-"))
    return date(year, number, start_day), date(next_year, next_number, start_day)


def period_of(day, start_day):
    label = f"{day.year:04d}-{day.month:02d}"
    return label if day.day >= start_day else month_shift(label, -1)


def start_day(session):
    item = session.get(AppSetting, START_DAY_KEY)
    return int(item.value) if item else DEFAULT_START_DAY


def set_start_day(session, day):
    validate_start_day(day)
    item = session.get(AppSetting, START_DAY_KEY)
    if item:
        item.value = str(day)
    else:
        session.add(AppSetting(key=START_DAY_KEY, value=str(day)))


def card_basis(session):
    item = session.get(AppSetting, CARD_BASIS_KEY)
    return item.value if item else DEFAULT_CARD_BASIS


def set_card_basis(session, basis):
    if basis not in CARD_BASES:
        raise ValueError("Card spending basis must be charge or purchase")
    item = session.get(AppSetting, CARD_BASIS_KEY)
    if item:
        item.value = basis
    else:
        session.add(AppSetting(key=CARD_BASIS_KEY, value=basis))


# On: every imported transaction waits for review, including rows a confirmed rule classified, since one
# merchant can sell for different purposes. The rule's category is kept as the suggestion.
REVIEW_ALL_KEY = "review_all_imports"


def review_all(session):
    item = session.get(AppSetting, REVIEW_ALL_KEY)
    return bool(item and item.value == "1")


def set_review_all(session, enabled):
    item = session.get(AppSetting, REVIEW_ALL_KEY)
    if item:
        item.value = "1" if enabled else "0"
    else:
        session.add(AppSetting(key=REVIEW_ALL_KEY, value="1" if enabled else "0"))


def budget_date(transaction, basis):
    """The date a transaction counts on in budgets and reports."""
    if basis == "charge" and transaction.card_id and not transaction.allocation_manual:
        return transaction.posting_date - timedelta(days=1)
    return transaction.allocation_date
