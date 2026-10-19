import re
import unicodedata
from decimal import Decimal, InvalidOperation
from datetime import date, datetime


def minor(value) -> int:
    if isinstance(value, float) or isinstance(value, bool):
        raise ValueError("Money must be a decimal string, never a float")
    text = str(value).strip().replace("₪", "").replace("\u00a0", "").strip()
    if text.startswith("(") and text.endswith(")"):
        text = "-" + text[1:-1]
    if "," in text:
        if not re.fullmatch(r"-?\d{1,3}(,\d{3})+(\.\d{1,2})?", text):
            raise ValueError("Ambiguous decimal/grouping separator")
        text = text.replace(",", "")
    try:
        amount = Decimal(text)
    except InvalidOperation:
        raise ValueError("Invalid amount") from None
    if not amount.is_finite() or abs(amount) > Decimal("10000000000"):
        raise ValueError("Amount outside permitted range")
    cents = amount * 100
    if cents != cents.to_integral_value():
        raise ValueError("Amount has more than two decimal places")
    return int(cents)


def money(value: int) -> str:
    return format(Decimal(value) / 100, ".2f")


def normalized(value: str) -> str:
    text = unicodedata.normalize("NFKC", value)
    text = "".join(c for c in text if unicodedata.category(c) != "Cf")
    return " ".join(text.split()).casefold()


def parse_date(value) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    for pattern in ("%Y-%m-%d", "%d/%m/%Y", "%d.%m.%Y"):
        try:
            return datetime.strptime(str(value).strip(), pattern).date()
        except ValueError:
            pass
    raise ValueError("Date must include a four-digit year (YYYY-MM-DD or DD/MM/YYYY)")
