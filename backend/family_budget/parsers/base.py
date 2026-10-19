"""Explicit interchange contracts. These column names are ours, not invented bank fields."""

import io
import csv
import json
import subprocess
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path
import pandas as pd
from ..normalization.money import minor, normalized, parse_date


class ParserError(ValueError):
    pass


KINDS = {
    "purchase",
    "refund",
    "income",
    "card_settlement",
    "internal_transfer",
    "cash_withdrawal",
    "loan_received",
    "loan_payment",
    "mortgage",
    "savings_transfer",
    "investment_transfer",
    "investment_sale",
    "reimbursement",
    "interest",
}
FIELDS = {
    "transaction_date",
    "posting_date",
    "allocation_date",
    "merchant",
    "description",
    "amount",
    "currency",
    "original_amount",
    "original_currency",
    "reference",
    "kind",
    "purchase_id",
    "installment_number",
    "installment_count",
    "original_purchase_amount",
    "remaining_commitment",
}
REQUIRED = {"transaction_date", "posting_date", "merchant", "amount", "currency"}


@dataclass
class ParsedRow:
    row_number: int
    page: int | None
    data: dict
    error: str | None = None


def normalize_row(raw: dict, sign: str) -> dict:
    if not REQUIRED.issubset(raw):
        raise ValueError("Required columns missing")
    tx_date, posting = parse_date(raw["transaction_date"]), parse_date(raw["posting_date"])
    amount = minor(raw["amount"])
    if sign == "debit_positive":
        amount = -amount
    currency = raw["currency"].strip().upper()
    if len(currency) != 3 or not currency.isascii() or not currency.isalpha():
        raise ValueError("Use a three-letter currency code")
    merchant = str(raw["merchant"]).strip()
    if len(raw.get("description", "")) > 4000 or len(raw.get("reference", "")) > 500:
        raise ValueError("Description or reference is too long")
    if not merchant or len(merchant) > 500:
        raise ValueError("Merchant is empty or too long")
    original_currency = (raw.get("original_currency") or currency).strip().upper()
    if len(original_currency) != 3 or not original_currency.isascii() or not original_currency.isalpha():
        raise ValueError("Original currency must be a three-letter code")
    if original_currency != currency and not raw.get("original_amount"):
        raise ValueError("Foreign currency needs its original amount")
    original = minor(raw.get("original_amount") or raw["amount"])
    if sign == "debit_positive":
        original = -original
    if amount == 0:
        raise ValueError("Zero amount is not a financial movement")
    kind = raw.get("kind") or ("purchase" if amount < 0 else "refund")
    if kind not in KINDS:
        raise ValueError("Unknown transaction kind")
    result = dict(
        transaction_date=tx_date.isoformat(),
        posting_date=posting.isoformat(),
        allocation_date=parse_date(
            raw.get("allocation_date") or (posting if raw.get("installment_number") else tx_date)
        ).isoformat(),
        merchant=merchant,
        raw_description=raw.get("description") or merchant,
        amount_minor=amount,
        currency=currency,
        original_amount_minor=original,
        original_currency=original_currency,
        kind=kind,
        source_reference=raw.get("reference") or None,
    )
    installment = [
        raw.get(k)
        for k in (
            "purchase_id",
            "installment_number",
            "installment_count",
            "original_purchase_amount",
            "remaining_commitment",
        )
    ]
    if any(installment):
        if not all(installment):
            raise ValueError(
                "Installments require purchase_id, number, count, original total and remaining commitment"
            )
        number, count = int(installment[1]), int(installment[2])
        total, remaining = minor(installment[3]), minor(installment[4])
        if (
            not 1 <= number <= count <= 600
            or total <= 0
            or not 0 <= remaining <= total
            or abs(amount) > total
        ):
            raise ValueError("Invalid installment amounts or sequence")
        if number == count and remaining != 0:
            raise ValueError("Final installment must have zero remaining commitment")
        result.update(
            purchase_id=installment[0],
            installment_number=number,
            installment_count=count,
            original_purchase_minor=total,
            remaining_commitment_minor=remaining,
        )
    return result


def validate_upload(content: bytes, extension: str):
    if extension not in {".csv", ".xlsx", ".pdf", ".json"}:
        raise ParserError("Supported file types: CSV, XLSX, PDF, fetcher JSON")
    if not content:
        raise ParserError("Empty document")
    if extension == ".pdf" and not content.startswith(b"%PDF-"):
        raise ParserError("Invalid PDF signature")
    if extension == ".xlsx":
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                infos = archive.infolist()
                if len(infos) > 2000 or sum(x.file_size for x in infos) > 80 * 1024 * 1024:
                    raise ParserError("Spreadsheet exceeds decompression limit")
                if "xl/workbook.xml" not in archive.namelist():
                    raise ParserError("Not an XLSX workbook")
        except zipfile.BadZipFile:
            raise ParserError("Invalid XLSX signature") from None


def extract_pdf(path: Path) -> list:
    try:
        result = subprocess.run(
            [sys.executable, "-m", "family_budget.parsers.pdf_worker", str(path)],
            capture_output=True,
            timeout=20,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise ParserError("PDF extraction timed out; export CSV/XLSX instead") from None
    if result.returncode != 0:
        raise ParserError(
            "PDF extraction unreliable, encrypted, scanned or oversized; export CSV/XLSX instead"
        )
    try:
        return json.loads(result.stdout)
    except (ValueError, UnicodeDecodeError):
        raise ParserError("PDF extraction failed") from None


class MappedAdapter:
    institution = ""
    version = "mapped-v1-unverified-native"

    def __init__(self, card_last_four: str | None = None):
        self.card_last_four = card_last_four
        self.native: dict | None = None  # set by a validated native layout parser

    def parse(self, content: bytes, extension: str, path: Path, options: dict) -> list[ParsedRow]:
        validate_upload(content, extension)
        if extension == ".json":
            raise ParserError("JSON is accepted only as an Isracard or Bank Hapoalim fetcher export")
        if options.get("layout") not in {None, "user_mapped_table_v1"}:
            raise ParserError("Unknown layout contract")
        sign = options.get("sign", "signed")
        if sign not in {"signed", "debit_positive"}:
            raise ParserError("Choose signed or debit_positive amount convention")
        mapping = options.get("columns", {})
        if (
            not isinstance(mapping, dict)
            or set(mapping) - FIELDS
            or any(not isinstance(v, str) or not v for v in mapping.values())
        ):
            raise ParserError("Unknown canonical mapping fields")
        if extension == ".pdf":
            if not mapping or options.get("layout") != "user_mapped_table_v1":
                raise ParserError(
                    f"{self.institution}: native PDF layout is not validated. Supply a verified table mapping or export CSV/XLSX. Hebrew visual order is never guessed."
                )
            tables = extract_pdf(path)
        else:
            try:
                if extension == ".csv":
                    text = content.decode("utf-8-sig")
                    headers = next(csv.reader(io.StringIO(text)), [])
                    if len(headers) != len({normalized(h) for h in headers}):
                        raise ParserError("Duplicate table headers are ambiguous")
                    df = pd.read_csv(io.StringIO(text), dtype=str, keep_default_na=False, nrows=20001)
                else:
                    import openpyxl

                    workbook = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
                    try:
                        headers = next(workbook.worksheets[0].values, [])
                        if len(headers) != len({normalized(str(h or "")) for h in headers}):
                            raise ParserError("Duplicate table headers are ambiguous")
                    finally:
                        workbook.close()
                    df = pd.read_excel(
                        io.BytesIO(content), dtype=str, keep_default_na=False, engine="openpyxl", nrows=20001
                    )
                if len(df) > 20000:
                    raise ParserError("At most 20,000 records per document")
                tables = [{"page": None, "headers": list(df.columns), "rows": df.values.tolist()}]
            except ParserError:
                raise
            except Exception:
                raise ParserError(
                    "Unreadable table. CSV must be UTF-8; XLSX must contain a simple first-sheet table."
                ) from None
        output = []
        row_index = 1
        for table in tables:
            headers = [normalized(str(h or "")) for h in table["headers"]]
            if len(headers) != len(set(headers)):
                raise ParserError("Duplicate table headers are ambiguous")
            lookup = {field: normalized(mapping.get(field, field)) for field in FIELDS}
            if not all(lookup[field] in headers for field in REQUIRED):
                raise ParserError(
                    f"{self.institution}: unrecognized columns. Map transaction_date, posting_date, merchant, amount and currency explicitly."
                )
            for cells in table["rows"]:
                row_index += 1
                if row_index > 20001:
                    raise ParserError("At most 20,000 records per document")
                raw = {
                    field: str(cells[headers.index(column)] or "").strip()
                    for field, column in lookup.items()
                    if column in headers and headers.index(column) < len(cells)
                }
                try:
                    output.append(ParsedRow(row_index, table["page"], normalize_row(raw, sign)))
                except (ValueError, TypeError, KeyError) as error:
                    # Preserve rejected raw cells privately, never put them in logs.
                    output.append(
                        ParsedRow(
                            row_index,
                            table["page"],
                            {"raw": raw},
                            str(error)[:240] or "Invalid or incomplete row",
                        )
                    )
        if not output:
            raise ParserError("No readable transaction rows")
        return output
