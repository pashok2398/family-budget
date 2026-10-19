"""Bank Hapoalim current account fetched by israeli-bank-scrapers (fetcher/lib.mjs): hapoalim_scraper_json_v1.

Field mapping was read from israeli-bank-scrapers 6.12.2 (scrapers/hapoalim.js): date is the event date
(תאריך) and processedDate the value date (תאריך ערך), both UTC instants of Israeli midnights; amounts are
signed (debits negative) and always ILS; description is the bank's activity text (הפעולה) and memo the
transfer counterparty and message when the bank provides them. Transactions with serial number 0 are
reported as pending and skipped: the bank lists them again, final, on a later fetch.

identifier is the bank's reference (אסמכתא). It is not unique in a current account (fees, standing
orders and card charges share references), so it is kept in the description only and duplicates are
detected from the dates, amount and texts.
"""

from .base import ParsedRow, ParserError, normalize_row
from .isracard_scraper import amount, is_native, load, local_date

LAYOUT = "hapoalim_scraper_json_v1"


def parse(content: bytes, options: dict) -> tuple[list[ParsedRow], dict]:
    document = load(content)
    if not is_native(document) or document.get("company") != "hapoalim":
        raise ParserError("Not a Bank Hapoalim fetcher export (hapoalim_scraper_json_v1)")
    if options:
        raise ParserError("A Bank Hapoalim fetcher export takes no import options")
    account = str(document.get("account_number") or "")
    digits = "".join(ch for ch in account if ch.isdigit())
    if len(digits) < 4:
        raise ParserError("Fetcher export has no account number")
    txns = document.get("txns")
    if not isinstance(txns, list):
        raise ParserError("Fetcher export has no transaction list")

    output, skipped = [], 0
    for number, txn in enumerate(txns, 1):
        if not isinstance(txn, dict):
            output.append(ParsedRow(number, None, {"raw": {}}, "Transaction is not an object"))
            continue
        if txn.get("status") == "pending":
            skipped += 1
            continue
        raw = {}
        try:
            if (txn.get("chargedCurrency") or txn.get("originalCurrency") or "ILS") != "ILS":
                raise ValueError("Bank Hapoalim current account rows are expected in ILS")
            reference = str(txn.get("identifier") if txn.get("identifier") is not None else "").strip()
            memo = " ".join(str(txn.get("memo") or "").split())
            raw = {
                "transaction_date": local_date(txn.get("date")).isoformat(),
                "posting_date": local_date(txn.get("processedDate")).isoformat(),
                "merchant": " ".join(str(txn.get("description") or "").split()),
                "amount": amount(txn.get("chargedAmount")),
                "currency": "ILS",
                "description": " · ".join(filter(None, [memo, f"אסמכתא {reference}" if reference else ""])),
            }
            output.append(ParsedRow(number, None, normalize_row(raw, "signed")))
        except (ValueError, TypeError, KeyError) as error:
            output.append(ParsedRow(number, None, {"raw": raw}, str(error)[:240] or "Invalid transaction"))
    if not output:
        raise ParserError("This account has no final transactions in the fetched period")
    balance = document.get("balance")
    return output, {
        "layout": LAYOUT,
        "account_last_four": digits[-4:],
        "scraper_version": str(document.get("scraper_version") or ""),
        "fetched_at": str(document.get("fetched_at") or ""),
        "pending_skipped": skipped,
        "balance": amount(balance)
        if isinstance(balance, (int, float)) and not isinstance(balance, bool)
        else None,
    }
