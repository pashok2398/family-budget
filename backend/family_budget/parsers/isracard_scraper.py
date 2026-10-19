"""Isracard card account fetched by israeli-bank-scrapers (fetcher/fetch.mjs): isracard_scraper_json_v1.

Field mapping was read from israeli-bank-scrapers 6.12.2 (scrapers/base-isracard-amex.js):
identifier is the voucher number (מס' שובר) parsed as an integer, so leading zeros are restored to the
nine digits the website export prints; amounts are signed (expenses negative); processedDate is the card
billing date, or the actual charge date for off-cycle charges; dates are UTC instants of Israeli
midnights and are converted back to Asia/Jerusalem dates. Pending transactions are skipped, as in the
website export, and so are zero charges (benefits that were never billed).

Verified against a real fetch (7 cards, Aug-Nov 2026) compared with the website exports: every voucher
matched; merchant names are cut at 20 characters with punctuation turned into spaces (matching handles
that); for installments the library moves "date" forward by (number - 1) months (helpers/transactions.js
fixInstallments), which is undone here so the purchase date agrees with the website export, while the
installment is allocated to its charge date. The remaining commitment is not provided and never estimated.
"""

import json
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from .base import ParsedRow, ParserError, normalize_row
from .isracard_web import SYMBOLS

LAYOUT = "isracard_scraper_json_v1"
FORMAT = "israeli-bank-scrapers-account/v1"
ISRAEL = ZoneInfo("Asia/Jerusalem")


def load(content: bytes) -> dict | None:
    try:
        document = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    return document if isinstance(document, dict) else None


def is_native(document: dict | None) -> bool:
    return bool(document) and document.get("format") == FORMAT


def local_date(value) -> date:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(ISRAEL).date()
    except ValueError:
        raise ValueError("Unreadable transaction date") from None


def amount(value) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("Missing amount")
    try:
        return str(Decimal(repr(value)).quantize(Decimal("0.01")))
    except InvalidOperation:
        raise ValueError("Unreadable amount") from None


def currency(value) -> str:
    code = SYMBOLS.get(str(value or "").strip(), str(value or "").strip())
    if not code:
        raise ValueError("Missing currency")
    return code


def months_back(day: date, months: int) -> date:
    index = day.year * 12 + day.month - 1 - months
    year, month = divmod(index, 12)
    month += 1
    following = date(year + month // 12, month % 12 + 1, 1)
    return date(year, month, min(day.day, (following - date(year, month, 1)).days))


def reference(value) -> str:
    text = str(value if value is not None else "").strip()
    return text.zfill(9) if text.isdigit() else text


def parse(content: bytes, options: dict, card_last_four: str | None) -> tuple[list[ParsedRow], dict]:
    document = load(content)
    if not is_native(document):
        raise ParserError("Not an Isracard fetcher export (isracard_scraper_json_v1)")
    if document.get("company") != "isracard":
        raise ParserError("This fetcher export is not from Isracard")
    if options.get("allow_open") is not None:
        raise ParserError("allow_open applies only to the Isracard website export")
    account = str(document.get("account_number") or "")
    last_four = account[-4:]
    if not last_four.isdigit():
        raise ParserError("Fetcher export has no card number")
    if card_last_four and card_last_four != last_four:
        raise ParserError(
            f"This export is for card ending {last_four}, not the selected card ({card_last_four})"
        )
    txns = document.get("txns")
    if not isinstance(txns, list):
        raise ParserError("Fetcher export has no transaction list")

    output, skipped, zero = [], 0, 0
    billing = defaultdict(Decimal)
    for number, txn in enumerate(txns, 1):
        if not isinstance(txn, dict):
            output.append(ParsedRow(number, None, {"raw": {}}, "Transaction is not an object"))
            continue
        if txn.get("status") == "pending":
            skipped += 1
            continue
        if txn.get("chargedAmount") == 0:
            zero += 1
            continue
        raw = {}
        try:
            charged_currency = currency(txn.get("chargedCurrency"))
            note = " ".join(str(txn.get("memo") or "").split())  # Isracard pads some notes with double spaces
            purchased, posted = local_date(txn.get("date")), local_date(txn.get("processedDate"))
            plan = txn.get("installments") if isinstance(txn.get("installments"), dict) else None
            if plan:
                installment = f"תשלום {plan.get('number')} מתוך {plan.get('total')}"
                note = note if installment in note else " · ".join(filter(None, [note, installment]))
                purchased = months_back(purchased, int(plan.get("number") or 1) - 1)
            raw = {
                "transaction_date": purchased.isoformat(),
                "posting_date": posted.isoformat(),
                "merchant": str(txn.get("description") or "").strip(),
                "amount": amount(txn.get("chargedAmount")),
                "currency": charged_currency,
                "reference": reference(txn.get("identifier")),
                "description": note,
            }
            if plan:
                raw["allocation_date"] = raw["posting_date"]
            original_currency = currency(txn.get("originalCurrency") or charged_currency)
            if original_currency != charged_currency:
                raw["original_currency"] = original_currency
                raw["original_amount"] = amount(txn.get("originalAmount"))
            output.append(ParsedRow(number, None, normalize_row(raw, "signed")))
            billing[raw["posting_date"]] += Decimal(raw["amount"])
        except (ValueError, TypeError, KeyError) as error:
            output.append(ParsedRow(number, None, {"raw": raw}, str(error)[:240] or "Invalid transaction"))
    if not output and not (skipped or zero):
        raise ParserError("This card has no billed transactions in the fetched period")
    # Only pending or zero rows (e.g. a waived card fee): nothing to import yet, which is not an error.
    return output, {
        "layout": LAYOUT,
        "card_last_four": last_four,
        "scraper_version": str(document.get("scraper_version") or ""),
        "fetched_at": str(document.get("fetched_at") or ""),
        "pending_skipped": skipped,
        "zero_skipped": zero,
        "billing_totals": {day: str(-total) for day, total in sorted(billing.items())},
        # Billing dates still ahead: amounts can change and new purchases can join before the charge.
        "unbilled_dates": [day for day in sorted(billing) if day > datetime.now(ISRAEL).date().isoformat()],
    }
