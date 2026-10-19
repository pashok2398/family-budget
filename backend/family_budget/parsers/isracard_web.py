"""Isracard website "פירוט עסקאות" XLSX export, layout isracard_web_xlsx_v1.

Validated against real exports of two cards over two statement months (Oct/Nov 2026):
header (month/year, card name + last four digits, charge total, charge day), then sections
with fixed Hebrew headings. Imported sections:
  "עסקאות למועד חיוב"        posted on the statement charge date; must sum to its printed total
                              and to the header charge total
  "עסקאות בחיוב מחוץ למועד"  posted on the "חיוב בחשבון הבנק" date; no printed total exists
Skipped: "עסקאות שטרם נקלטו" (not final) and "עסקאות בחיוב עתידי" (summary only).
Amounts are the charged amount/currency; the purchase amount/currency is kept as the original.
Installments ("תשלום N מתוך M") stay ordinary charges: the export has no remaining commitment.
"""

import io
import re
from decimal import Decimal, InvalidOperation

from .base import ParsedRow, ParserError, normalize_row

LAYOUT = "isracard_web_xlsx_v1"
TITLE = "פירוט עסקאות"
BIDI = re.compile(r"[\u200e\u200f\u202a-\u202e\u2066-\u2069]")
SYMBOLS = {"₪": "ILS", "$": "USD", "€": "EUR", "L": "RON"}  # Isracard prints the Romanian leu as "L"
SECTIONS = {"עסקאות למועד חיוב": "scheduled", "עסקאות בחיוב מחוץ למועד": "off_cycle"}
SKIPPED = {"עסקאות שטרם נקלטו", "עסקאות בחיוב עתידי"}
HEADERS = ["תאריך רכישה", "שם בית עסק", "סכום עסקה", "מטבע עסקה", "סכום חיוב", "מטבע חיוב", "מס' שובר"]
MONTHS = {
    name: number
    for number, name in enumerate(
        [
            "ינואר",
            "פברואר",
            "מרץ",
            "אפריל",
            "מאי",
            "יוני",
            "יולי",
            "אוגוסט",
            "ספטמבר",
            "אוקטובר",
            "נובמבר",
            "דצמבר",
        ],
        1,
    )
}
NOT_FINAL = "החיוב אינו סופי"
INSTALLMENT = re.compile(r"תשלום \d+ מתוך \d+")
DATE = re.compile(r"(\d{2})\.(\d{2})\.(\d{2}|\d{4})")


def text(cell) -> str:
    return BIDI.sub("", str(cell if cell is not None else "")).strip()


def load_rows(content: bytes) -> list[list]:
    import openpyxl

    workbook = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    try:
        return [list(row) + [None] * 9 for row in workbook.worksheets[0].iter_rows(values_only=True)]
    finally:
        workbook.close()


def is_native(rows: list[list]) -> bool:
    return any(text(row[0]) == TITLE for row in rows[:5]) and any(text(row[0]) in SECTIONS for row in rows)


def iso_date(value, number: int) -> str:
    match = DATE.fullmatch(text(value))
    if not match:
        raise ParserError(f"Isracard row {number}: unexpected date format")
    day, month, year = match.groups()
    return f"{'20' + year if len(year) == 2 else year}-{month}-{day}"


def money(value, number: int) -> Decimal:
    try:
        return Decimal(text(value).replace(",", "").replace("₪", "").strip()).quantize(Decimal("0.01"))
    except InvalidOperation:
        raise ParserError(f"Isracard row {number}: unreadable amount") from None


def parse(content: bytes, options: dict, card_last_four: str | None) -> tuple[list[ParsedRow], dict]:
    rows = load_rows(content)
    if not is_native(rows):
        raise ParserError("Not an Isracard website transaction export (isracard_web_xlsx_v1)")
    year = month = charge = card = header_total = None
    is_open = False
    for row in rows[:10]:
        cells = [text(c) for c in row]
        on_card_row = False
        for cell in cells:
            if m := re.fullmatch(r"(.+?) - (\d{4})", cell):
                card, on_card_row = (m.group(1).strip(), m.group(2)), True
            if (m := re.fullmatch(r"(\S+) (\d{4})", cell)) and m.group(1) in MONTHS:
                month, year = MONTHS[m.group(1)], int(m.group(2))
            if m := re.search(r"לחיוב ב-(\d{2})\.(\d{2})", cell):
                charge = m.groups()
            is_open = is_open or NOT_FINAL in cell
        # The charge total shares the card's row; other ₪ header cells are credit limit/remaining.
        for cell in cells if on_card_row else []:
            if re.fullmatch(r"₪ ?-?[\d,]+\.\d{2}", cell):
                header_total = money(cell, 5)
    if not (year and charge and card and header_total is not None):
        raise ParserError("Isracard export header (month, card, charge date or total) not found")
    if int(charge[1]) != month:
        raise ParserError("Isracard charge date month differs from the statement month")
    if card_last_four and card_last_four != card[1]:
        raise ParserError(
            f"This export is for card ending {card[1]}, not the selected card ({card_last_four})"
        )
    if is_open and not options.get("allow_open"):
        raise ParserError(
            'Isracard marks this statement as not final (החיוב אינו סופי). Export again after the charge date, or add {"allow_open": true}.'
        )
    posting = f"{year}-{charge[1]}-{charge[0]}"

    output, section, printed_total = [], None, None
    totals = {"scheduled": Decimal(0), "off_cycle": Decimal(0)}
    for number, row in enumerate(rows, 1):
        first = text(row[0])
        if first in SECTIONS:
            section = SECTIONS[first]
            continue
        if first in SKIPPED:
            section = None
            continue
        if section is None:
            continue
        if first == HEADERS[0]:
            if [text(c) for c in row[:7]] != HEADERS:
                raise ParserError(f"Isracard row {number}: unexpected column headers")
            continue
        if not first:
            if section == "scheduled" and row[4] is not None:  # section total row
                printed_total = money(row[4], number)
            continue
        if not DATE.fullmatch(first):  # explanatory footer text ends the section
            section = None
            continue
        charged = money(row[4], number)
        totals[section] += charged
        symbol = text(row[3])
        note = " · ".join(p.strip() for p in str(row[7] or "").split("\n") if p.strip())
        raw = {
            "transaction_date": iso_date(row[0], number),
            "posting_date": posting if section == "scheduled" else iso_date(row[8], number),
            "merchant": text(row[1]),
            "amount": str(-charged),
            "currency": SYMBOLS.get(text(row[5]), text(row[5])),
            "reference": text(row[6]),
            "description": note,
        }
        if INSTALLMENT.search(note):
            raw["allocation_date"] = raw["posting_date"]  # each installment belongs to its charge month
        if symbol in SYMBOLS:
            raw["original_currency"] = SYMBOLS[symbol]
            raw["original_amount"] = str(-money(row[2], number))
        else:
            raw["description"] = " · ".join(
                filter(None, [note, f"סכום מקורי {money(row[2], number)} {symbol}"])
            )
        try:
            output.append(ParsedRow(number, None, normalize_row(raw, "signed")))
        except (ValueError, TypeError, KeyError) as error:
            output.append(ParsedRow(number, None, {"raw": raw}, str(error)[:240] or "Invalid row"))
    if not output:
        raise ParserError("No readable transaction rows")
    if printed_total is None:
        raise ParserError("Isracard printed total for 'עסקאות למועד חיוב' not found")
    if printed_total != totals["scheduled"] or header_total != printed_total:
        raise ParserError(
            f"Isracard totals disagree: rows {totals['scheduled']}, section {printed_total}, header {header_total}"
        )
    return output, {
        "layout": LAYOUT,
        "card_name": card[0],
        "card_last_four": card[1],
        "charge_date": posting,
        "statement_final": not is_open,
        "scheduled_total": str(printed_total),
        "off_cycle_total": str(totals["off_cycle"]),
    }
