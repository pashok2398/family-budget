import hashlib
import json
import os
import re
from pathlib import Path
from collections import Counter
from datetime import date
from sqlalchemy import select, delete
from ..models import Account, CreditCard, SourceDocument, ImportBatch, ImportRow, Transaction, AuditEvent
from ..parsers.base import ParsedRow, ParserError, validate_upload
from ..parsers.hapoalim import HapoalimAdapter
from ..parsers.isracard import IsracardAdapter
from ..normalization.money import normalized, minor
from ..config import MAX_UPLOAD

ADAPTERS = {"hapoalim": HapoalimAdapter, "isracard": IsracardAdapter}


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def merchant_key(name):
    return re.sub(r"[\W_]+", "", name.casefold())


def same_merchant(a, b):
    # The Isracard fetcher cuts names at 20 characters and turns punctuation into spaces.
    short, full = sorted((merchant_key(a), merchant_key(b)), key=len)
    return short == full or (len(short) >= 8 and full.startswith(short))


def same_financial_details(existing, data):
    # A stable source reference with identical money, purchase date and merchant is the same transaction, even
    # when another export channel words the notes, original amount or merchant name differently, or the card
    # company moves the charge to a later billing date (refunds and forex orders get re-billed).
    return (
        existing.transaction_date.isoformat() == data["transaction_date"]
        and existing.amount_minor == data["amount_minor"]
        and existing.currency == data["currency"]
        and same_merchant(existing.merchant, data["merchant"])
    )


def rebill(session, transaction, row, fetched):
    """Take the latest charge date a statement reports for a referenced transaction matched on re-import.

    Fetcher exports are skipped: their processedDate is the card's bill date even for off-cycle charges
    (refunds, forex orders), which the web statement lists with their real bank debit date.
    """
    posting = date.fromisoformat(row.data["posting_date"])
    if (
        fetched
        or not row.source_reference
        or transaction.source_reference != row.source_reference
        or transaction.posting_date == posting
    ):
        return
    before = {
        "posting_date": str(transaction.posting_date),
        "allocation_date": str(transaction.allocation_date),
    }
    transaction.posting_date = posting
    if not transaction.allocation_manual:
        transaction.allocation_date = date.fromisoformat(row.data["allocation_date"])
    transaction.base_fingerprint = row.base_fingerprint
    session.add(
        AuditEvent(
            entity_type="transaction",
            entity_id=transaction.id,
            action="rebill",
            before=before,
            after={
                "posting_date": str(transaction.posting_date),
                "allocation_date": str(transaction.allocation_date),
            },
        )
    )


def identities(data, source_key, occurrence):
    base = digest(
        [
            source_key,
            data["transaction_date"],
            data["posting_date"],
            data["amount_minor"],
            data["currency"],
            data["original_amount_minor"],
            data["original_currency"],
            normalized(data["merchant"]),
            normalized(data["raw_description"]),
            data.get("purchase_id"),
            data.get("installment_number"),
            data.get("installment_count"),
            data.get("original_purchase_minor"),
            data.get("remaining_commitment_minor"),
        ]
    )
    fingerprint = (
        digest([source_key, "reference", data["source_reference"]])
        if data["source_reference"]
        else digest([base, occurrence])
    )
    if not data["source_reference"] and data.get("purchase_id"):
        fingerprint = digest([source_key, "installment", data["purchase_id"], data["installment_number"]])
    return base, fingerprint


def preserve(content, extension, root):
    sha = hashlib.sha256(content).hexdigest()
    folder = root / "imports"
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = folder / f"{sha}{extension}"
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
    except FileExistsError:
        if hashlib.sha256(path.read_bytes()).hexdigest() != sha:
            raise ValueError("Stored source document integrity check failed")
    return sha, path


def assess(session, parsed, source_key, whole_days=False):
    """Duplicate status of each parsed row against the ledger: new, matched, ambiguous or rejected.

    whole_days: the document is a fetcher export, which always covers whole days, so the n-th identical
    movement of a day in it is the n-th identical movement already imported from the same source (bank
    rows carry no unique reference). Statements uploaded by hand keep identical rows ambiguous.
    """
    occurrence = Counter()
    reference_seen = set()
    installment_seen = set()
    for parsed_row in parsed:
        data, error = parsed_row.data, parsed_row.error
        base = fingerprint = reference = None
        candidates = []
        status = "rejected" if error else "new"
        if not error:
            base, _ = identities(data, source_key, 0)
            occurrence[base] += 1
            base, fingerprint = identities(data, source_key, occurrence[base])
            reference = data["source_reference"]
            if reference and reference in reference_seen:
                status, error = (
                    "rejected",
                    "Repeated source reference within statement; source must be corrected",
                )
            elif reference:
                reference_seen.add(reference)
                existing = session.scalar(
                    select(Transaction).where(
                        Transaction.source_key == source_key, Transaction.source_reference == reference
                    )
                )
                if existing:
                    candidates = [existing.id]
                    status = (
                        "matched"
                        if existing.base_fingerprint == base or same_financial_details(existing, data)
                        else "ambiguous"
                    )
            else:
                candidates = list(
                    session.scalars(select(Transaction.id).where(Transaction.base_fingerprint == base))
                )
                if candidates:
                    status = "ambiguous"
                    same = whole_days and session.scalar(
                        select(Transaction).where(
                            Transaction.source_key == source_key, Transaction.fingerprint == fingerprint
                        )
                    )
                    if same:
                        candidates, status = [same.id], "matched"
        if not error and data.get("purchase_id"):
            purchase_key = (data["purchase_id"], data["installment_number"])
            if purchase_key in installment_seen:
                status, error = "rejected", "Repeated installment identity within the statement"
            else:
                installment_seen.add(purchase_key)
                existing_installment = session.scalar(
                    select(Transaction).where(
                        Transaction.source_key == source_key,
                        Transaction.purchase_id == data["purchase_id"],
                        Transaction.installment_number == data["installment_number"],
                    )
                )
                if existing_installment:
                    candidates = list(set(candidates) | {existing_installment.id})
                    status = "matched" if existing_installment.base_fingerprint == base else "ambiguous"
                purchase_history = list(
                    session.scalars(
                        select(Transaction).where(
                            Transaction.source_key == source_key,
                            Transaction.purchase_id == data["purchase_id"],
                        )
                    )
                )
                if any(
                    t.installment_count != data["installment_count"]
                    or t.original_purchase_minor != data["original_purchase_minor"]
                    for t in purchase_history
                ):
                    status, error = (
                        "rejected",
                        "Installment purchase total/count conflicts with existing purchase history",
                    )
        yield parsed_row, data, error, status, candidates, fingerprint, base, reference


def pending_rows(session, content, filename, account_id, card_id, adapter):
    """What a fetched file would bring, without creating an import: counts per duplicate status."""
    card = session.get(CreditCard, card_id) if card_id else None
    parser = ADAPTERS[adapter](card_last_four=card.last_four if card else None)
    extension = Path(filename).suffix.lower()
    parsed = parser.parse(content, extension, Path(filename), {})
    source_key = f"card:{card_id}" if card_id else f"account:{account_id}"
    whole_days = parser.native is not None and extension == ".json"
    return Counter(status for _, _, _, status, *_ in assess(session, parsed, source_key, whole_days))


def preview(
    session,
    root: Path,
    content: bytes,
    filename: str,
    account_id: str,
    card_id: str | None,
    adapter: str,
    options: dict,
):
    if len(content) > MAX_UPLOAD:
        raise ParserError("File exceeds 20 MiB")
    extension = Path(filename).suffix.lower()
    validate_upload(content, extension)
    account = session.get(Account, account_id)
    if not account:
        raise ValueError("Unknown account")
    card = session.get(CreditCard, card_id) if card_id else None
    if card_id and (not card or card.account_id != account_id):
        raise ValueError("Card does not belong to account")
    if adapter not in ADAPTERS:
        raise ParserError("Unknown parser adapter")
    if adapter == "isracard" and not card:
        raise ValueError("Isracard imports require a credit card source")
    if adapter == "hapoalim" and card:
        raise ValueError("Hapoalim imports use a bank account source")
    if any(options.get(key) is not None and not isinstance(options[key], str) for key in ("sign", "layout")):
        raise ParserError("Layout and sign options must be strings")
    if set(options) - {"columns", "sign", "layout", "expected_total", "allow_open"}:
        raise ParserError("Unknown parser options")
    if options.get("allow_open") is not None and not isinstance(options["allow_open"], bool):
        raise ParserError("allow_open must be true or false")
    if options.get("expected_total") is not None and not isinstance(options["expected_total"], str):
        raise ParserError("Expected total must be a decimal string")
    source_key = f"card:{card_id}" if card_id else f"account:{account_id}"
    sha, path = preserve(content, extension, root)
    document = session.scalar(select(SourceDocument).where(SourceDocument.sha256 == sha))
    batch = None
    if document:
        batch = session.scalar(
            select(ImportBatch).where(
                ImportBatch.document_id == document.id, ImportBatch.source_key == source_key
            )
        )
        if batch and (batch.status == "committed" or batch.summary.get("parser_options") == options):
            return batch
    parser = ADAPTERS[adapter](card_last_four=card.last_four if card else None)
    parsed = parser.parse(content, extension, path, options)
    if not document:
        document = SourceDocument(sha256=sha, extension=extension, size=len(content))
        session.add(document)
        session.flush()
    if batch:
        session.add(
            AuditEvent(
                entity_type="import_batch",
                entity_id=batch.id,
                action="reparse",
                before={"parser_options": batch.summary.get("parser_options")},
                after={"parser_options": options},
            )
        )
        session.execute(delete(ImportRow).where(ImportRow.batch_id == batch.id))
    else:
        batch = ImportBatch(
            document_id=document.id,
            account_id=account_id,
            card_id=card_id,
            source_key=source_key,
            parser=f"{adapter}:{parser.version}",
            summary={},
        )
        session.add(batch)
        session.flush()
    counts = Counter()
    whole_days = parser.native is not None and extension == ".json"
    for parsed_row, data, error, status, candidates, fingerprint, base, reference in assess(
        session, parsed, source_key, whole_days
    ):
        counts[status] += 1
        session.add(
            ImportRow(
                batch_id=batch.id,
                row_number=parsed_row.row_number,
                page=parsed_row.page,
                data=data,
                status=status,
                error=error,
                fingerprint=fingerprint,
                base_fingerprint=base,
                source_reference=reference,
                candidates=candidates,
            )
        )
    total = sum(r.data["amount_minor"] for r in parsed if not r.error)
    currencies = {r.data["currency"] for r in parsed if not r.error}
    expected = options.get("expected_total")
    validated = (
        expected is not None and len(currencies) == 1 and not counts["rejected"] and minor(expected) == total
    )
    mismatch = expected is not None and not validated
    batch.summary = {
        **{k: counts[k] for k in ("new", "matched", "ambiguous", "rejected")},
        "extracted_total_minor": total if len(currencies) == 1 else None,
        "expected_total": expected,
        "total_validated": validated,
        "total_mismatch": mismatch,
        "parser_options": options,
        "native_layout_validated": parser.native is not None,
        "native": parser.native,
    }
    return batch


def resolve_row(session, row_id, resolution):
    row = session.get(ImportRow, row_id)
    if not row:
        raise ValueError("Unknown import row")
    batch = session.get(ImportBatch, row.batch_id)
    if batch.status != "preview":
        raise ValueError("Batch is already committed")
    allowed = {"new", "reject"} | {f"duplicate:{x}" for x in row.candidates}
    if resolution not in allowed or (row.status == "rejected" and resolution != "reject"):
        raise ValueError("Invalid resolution")
    if (row.source_reference or row.data.get("purchase_id")) and row.candidates and resolution == "new":
        raise ValueError("Conflicting source reference cannot create a new transaction")
    if row.status == "matched" and resolution == "new":
        raise ValueError("Confirmed source reference is already imported")
    before = {"resolution": row.resolution}
    row.resolution = resolution
    session.add(
        AuditEvent(
            entity_type="import_row",
            entity_id=row.id,
            action="resolve",
            before=before,
            after={"resolution": resolution},
        )
    )
    return row


def commit(session, batch_id):
    from ..classification.engine import classify

    batch = session.get(ImportBatch, batch_id)
    if not batch:
        raise ValueError("Unknown import batch")
    if batch.status == "committed":
        return batch
    if batch.summary.get("total_mismatch"):
        raise ValueError("Statement total mismatch; correct the input before committing")
    rows = list(
        session.scalars(
            select(ImportRow).where(ImportRow.batch_id == batch_id).order_by(ImportRow.row_number)
        )
    )
    if any(r.status in {"ambiguous", "rejected"} and not r.resolution for r in rows):
        raise ValueError("Resolve every ambiguous/rejected row explicitly before committing")
    duplicate_targets = [
        r.resolution.split(":", 1)[1] for r in rows if r.resolution and r.resolution.startswith("duplicate:")
    ]
    if len(duplicate_targets) != len(set(duplicate_targets)):
        raise ValueError("Two rows cannot both be the same historical transaction; review occurrences")
    document = session.get(SourceDocument, batch.document_id)
    fetched = batch.summary.get("native") is not None and document.extension == ".json"  # see assess()
    for row in rows:
        if row.resolution == "reject":
            row.status = "rejected"
            continue
        if row.resolution and row.resolution.startswith("duplicate:"):
            row.transaction_id = row.resolution.split(":", 1)[1]
            row.status = "matched"
            rebill(session, session.get(Transaction, row.transaction_id), row, fetched)
            continue
        existing = session.scalar(select(Transaction).where(Transaction.fingerprint == row.fingerprint))
        if row.status == "matched":
            row.transaction_id = row.candidates[0]
            rebill(session, session.get(Transaction, row.transaction_id), row, fetched)
            continue
        # Recheck after other previews have committed, including explicitly new overlap rows.
        overlaps = list(
            session.scalars(
                select(Transaction).where(
                    Transaction.base_fingerprint == row.base_fingerprint,
                    Transaction.document_id != batch.document_id,
                )
            )
        )
        unseen = [t for t in overlaps if t.id not in row.candidates]
        if unseen and not row.source_reference:
            raise ValueError(
                "Historical transactions changed since preview; refresh this batch before committing"
            )
        if (row.source_reference or row.data.get("purchase_id")) and existing:
            if existing.base_fingerprint != row.base_fingerprint:
                raise ValueError("Source reference conflict; refresh preview")
            row.transaction_id, row.status = existing.id, "matched"
            continue
        fingerprint = row.fingerprint
        if row.resolution == "new" and existing:
            fingerprint = digest([row.fingerprint, batch.document_id, row.row_number])
        data = dict(row.data)
        for key in ("transaction_date", "posting_date", "allocation_date"):
            data[key] = date.fromisoformat(data[key])
        transaction = Transaction(
            **data,
            account_id=batch.account_id,
            card_id=batch.card_id,
            document_id=batch.document_id,
            source_row=row.row_number,
            source_page=row.page,
            source_key=batch.source_key,
            fingerprint=fingerprint,
            base_fingerprint=row.base_fingerprint,
        )
        session.add(transaction)
        session.flush()
        classify(session, transaction)
        row.transaction_id, row.status = transaction.id, "new"
    batch.status = "committed"
    counts = Counter(r.status for r in rows)
    batch.summary = {**batch.summary, "committed_counts": dict(counts)}
    session.add(
        AuditEvent(
            entity_type="import_batch",
            entity_id=batch.id,
            action="commit",
            before={},
            after={"counts": dict(counts)},
        )
    )
    return batch


def refresh(session, batch_id):
    """Recheck an open preview against what was imported since, with the same rules as the preview."""
    batch = session.get(ImportBatch, batch_id)
    if not batch or batch.status != "preview":
        raise ValueError("Only previews can be refreshed")
    document = session.get(SourceDocument, batch.document_id)
    whole_days = batch.summary.get("native") is not None and document.extension == ".json"  # see assess()
    rows = list(
        session.scalars(
            select(ImportRow).where(ImportRow.batch_id == batch_id).order_by(ImportRow.row_number)
        )
    )
    parsed = [ParsedRow(r.row_number, r.page, r.data, r.error) for r in rows]
    for row, (_, _, error, status, candidates, *_) in zip(
        rows, assess(session, parsed, batch.source_key, whole_days)
    ):
        if row.error:
            continue
        if set(candidates) != set(row.candidates):
            row.resolution = None  # a decision about other candidates no longer applies
        row.candidates, row.status, row.error = candidates, status, error
    statuses = Counter(r.status for r in rows)
    batch.summary = {**batch.summary, **{k: statuses[k] for k in ("new", "matched", "ambiguous", "rejected")}}
    return batch
