import json
import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("pandas")
from sqlalchemy import select, func
from family_budget.db import session_scope
from family_budget.models import Transaction, ImportRow
from family_budget.ingestion.service import preview, commit, resolve_row, refresh


def csv(rows, reference=False):
    header = (
        "transaction_date,posting_date,merchant,amount,currency" + (",reference" if reference else "") + "\n"
    )
    return (header + "\n".join(rows) + "\n").encode()


def test_document_and_reference_idempotency(ledger, fixture_dir):
    engine, root, account, card = ledger
    content = (fixture_dir / "isracard_canonical.csv").read_bytes()
    with session_scope(engine, True) as s:
        b = preview(s, root, content, "x.csv", account, card, "isracard", {})
        commit(s, b.id)
        same = preview(s, root, content, "renamed.csv", account, card, "isracard", {})
        assert same.id == b.id
        commit(s, same.id)
        assert s.scalar(select(func.count()).select_from(Transaction)) == 2
    with session_scope(engine, True) as s:
        # Semantically identical new document, with a trailing blank line.
        b = preview(s, root, content + b"\n", "x.csv", account, card, "isracard", {})
        assert b.summary["matched"] == 2
        commit(s, b.id)
        assert s.scalar(select(func.count()).select_from(Transaction)) == 2


def test_identical_legitimate_purchases_and_overlap(ledger):
    engine, root, account, card = ledger
    content = csv(["2026-10-05,2026-10-10,Same store,-10.00,ILS"] * 2)
    with session_scope(engine, True) as s:
        b = preview(s, root, content, "x.csv", account, card, "isracard", {})
        commit(s, b.id)
        assert s.scalar(select(func.count()).select_from(Transaction)) == 2
    with session_scope(engine, True) as s:
        b = preview(s, root, content + b"\n", "other.csv", account, card, "isracard", {})
        assert b.summary["ambiguous"] == 2
        rows = list(s.scalars(select(ImportRow).where(ImportRow.batch_id == b.id)))
        resolve_row(s, rows[0].id, "duplicate:" + rows[0].candidates[0])
        resolve_row(s, rows[1].id, "new")
        commit(s, b.id)
        assert s.scalar(select(func.count()).select_from(Transaction)) == 3


def test_ambiguous_never_silent(ledger):
    engine, root, account, card = ledger
    content = csv(["2026-10-05,2026-10-10,Same store,-10.00,ILS"])
    with session_scope(engine, True) as s:
        b = preview(s, root, content, "x.csv", account, card, "isracard", {})
        commit(s, b.id)
    with session_scope(engine, True) as s:
        b = preview(s, root, content + b"\n", "x.csv", account, card, "isracard", {})
        with pytest.raises(ValueError, match="Resolve every"):
            commit(s, b.id)


def test_total_mismatch_blocks(ledger):
    engine, root, account, card = ledger
    with session_scope(engine, True) as s:
        b = preview(
            s,
            root,
            csv(["2026-10-05,2026-10-10,Store,-10,ILS"]),
            "x.csv",
            account,
            card,
            "isracard",
            {"expected_total": "-11.00"},
        )
        assert b.summary["total_mismatch"]
        with pytest.raises(ValueError, match="total mismatch"):
            commit(s, b.id)


def test_rejected_requires_acknowledgment(ledger):
    engine, root, account, card = ledger
    with session_scope(engine, True) as s:
        b = preview(s, root, csv(["bad,2026-10-10,Store,-10,ILS"]), "x.csv", account, card, "isracard", {})
        row = s.scalar(select(ImportRow).where(ImportRow.batch_id == b.id))
        assert row.status == "rejected"
        with pytest.raises(ValueError):
            commit(s, b.id)
        resolve_row(s, row.id, "reject")
        commit(s, b.id)
        assert s.scalar(select(func.count()).select_from(Transaction)) == 0


def test_stale_preview_cannot_double_import(ledger):
    engine, root, account, card = ledger
    content = csv(["2026-10-05,2026-10-10,Store,-10,ILS"])
    with session_scope(engine, True) as s:
        a = preview(s, root, content, "x.csv", account, card, "isracard", {})
        b = preview(s, root, content + b"\n", "y.csv", account, card, "isracard", {})
        a_id, b_id = a.id, b.id
        commit(s, a_id)
    with session_scope(engine, True) as s:
        with pytest.raises(ValueError, match="changed since preview"):
            commit(s, b_id)
        refresh(s, b_id)
        row = s.scalar(select(ImportRow).where(ImportRow.batch_id == b_id))
        assert row.status == "ambiguous"
        resolve_row(s, row.id, "duplicate:" + row.candidates[0])
        commit(s, b_id)
        assert s.scalar(select(func.count()).select_from(Transaction)) == 1


def test_source_reference_conflict_visible(ledger):
    engine, root, account, card = ledger
    with session_scope(engine, True) as s:
        a = preview(
            s,
            root,
            csv(["2026-10-05,2026-10-10,Store,-10,ILS,ref"], True),
            "a.csv",
            account,
            card,
            "isracard",
            {},
        )
        commit(s, a.id)
        b = preview(
            s,
            root,
            csv(["2026-10-05,2026-10-10,Store,-20,ILS,ref"], True),
            "b.csv",
            account,
            card,
            "isracard",
            {},
        )
        assert b.summary["ambiguous"] == 1
        row = s.scalar(select(ImportRow).where(ImportRow.batch_id == b.id))
        with pytest.raises(ValueError, match="Conflicting"):
            resolve_row(s, row.id, "new")


def test_changed_preview_options_can_fix_total(ledger):
    engine, root, account, card = ledger
    content = csv(["2026-10-05,2026-10-10,Store,-10,ILS"])
    with session_scope(engine, True) as s:
        first = preview(s, root, content, "x.csv", account, card, "isracard", {"expected_total": "-11"})
        batch_id = first.id
        fixed = preview(s, root, content, "x.csv", account, card, "isracard", {"expected_total": "-10"})
        assert fixed.id == batch_id and fixed.summary["total_validated"]
        commit(s, fixed.id)
        assert s.scalar(select(func.count()).select_from(Transaction)) == 1


def test_installment_identity_is_stable_and_repeated_charge_is_rejected(ledger):
    engine, root, account, card = ledger
    header = "transaction_date,posting_date,merchant,amount,currency,purchase_id,installment_number,installment_count,original_purchase_amount,remaining_commitment\n"
    row = "2026-09-01,2026-10-10,Store,-100,ILS,purchase-a,1,3,300,200\n"
    with session_scope(engine, True) as s:
        first = preview(s, root, (header + row).encode(), "a.csv", account, card, "isracard", {})
        commit(s, first.id)
        same = preview(s, root, (header + row + "\n").encode(), "b.csv", account, card, "isracard", {})
        assert same.summary["matched"] == 1
        commit(s, same.id)
        assert s.scalar(select(func.count()).select_from(Transaction)) == 1
        t = s.scalar(select(Transaction))
        assert t.allocation_date.isoformat() == "2026-10-10"


def test_isracard_web_export_preview_checks_selected_card(ledger):
    from test_parsers import isracard_web_export
    from family_budget.models import CreditCard
    from family_budget.parsers.base import ParserError

    engine, root, account, card = ledger
    with session_scope(engine, True) as s:
        s.get(CreditCard, card).last_four = "9999"
        with pytest.raises(ParserError, match="card ending 1234"):
            preview(s, root, isracard_web_export(), "x.xlsx", account, card, "isracard", {})
        s.get(CreditCard, card).last_four = "1234"
        with pytest.raises(ParserError, match="true or false"):
            preview(
                s, root, isracard_web_export(), "x.xlsx", account, card, "isracard", {"allow_open": "yes"}
            )
        b = preview(s, root, isracard_web_export(), "x.xlsx", account, card, "isracard", {})
        assert b.parser == "isracard:isracard_web_xlsx_v1"
        assert b.summary["native_layout_validated"] and b.summary["native"]["card_last_four"] == "1234"
        assert b.summary["new"] == 5 and not b.summary["total_mismatch"]
        commit(s, b.id)
        assert s.scalar(select(func.count()).select_from(Transaction)) == 5


def test_isracard_fetcher_and_website_export_recognize_each_other(ledger):
    from test_parsers import isracard_fetcher_export, isracard_web_export
    from family_budget.models import CreditCard

    engine, root, account, card = ledger
    with session_scope(engine, True) as s:
        s.get(CreditCard, card).last_four = "1234"
        website = preview(s, root, isracard_web_export(), "x.xlsx", account, card, "isracard", {})
        commit(s, website.id)
        fetched = preview(s, root, isracard_fetcher_export(), "x.json", account, card, "isracard", {})
        assert fetched.parser == "isracard:isracard_scraper_json_v1"
        assert fetched.summary["native"]["card_last_four"] == "1234"
        statuses = {
            r.data.get("merchant"): r.status
            for r in s.scalars(select(ImportRow).where(ImportRow.batch_id == fetched.id))
        }
        assert statuses.pop("Leading zero voucher") == "new"  # only in the fetcher file
        assert set(statuses.values()) == {"matched"}, statuses
        refresh(s, fetched.id)  # rechecking must not forget that truncated names are the same purchase
        rows = s.scalars(select(ImportRow).where(ImportRow.batch_id == fetched.id))
        assert {r.data.get("merchant"): r.status for r in rows} == {**statuses, "Leading zero voucher": "new"}


def test_same_reference_with_changed_amount_stays_ambiguous(ledger):
    from test_parsers import isracard_fetcher_export, isracard_web_export
    from family_budget.models import CreditCard

    engine, root, account, card = ledger
    with session_scope(engine, True) as s:
        s.get(CreditCard, card).last_four = "1234"
        commit(s, preview(s, root, isracard_web_export(), "x.xlsx", account, card, "isracard", {}).id)
        changed = json.loads(isracard_fetcher_export())
        changed["txns"][0]["chargedAmount"] = changed["txns"][0]["originalAmount"] = -121.25
        fetched = preview(s, root, json.dumps(changed).encode(), "x.json", account, card, "isracard", {})
        row = s.scalar(select(ImportRow).where(ImportRow.batch_id == fetched.id, ImportRow.row_number == 1))
        assert row.status == "ambiguous"


def test_bank_rules_apply_to_bank_movements_only(ledger):
    from test_parsers import hapoalim_fetcher_export, isracard_fetcher_export

    engine, root, account, card = ledger
    with session_scope(engine, True) as s:
        bank = preview(s, root, hapoalim_fetcher_export(), "b.json", account, None, "hapoalim", {})
        commit(s, bank.id)
        card_file = isracard_fetcher_export(
            txns=[
                {
                    "identifier": 1,
                    "date": "2026-10-01T21:00:00.000Z",
                    "processedDate": "2026-10-09T21:00:00.000Z",
                    "chargedAmount": -50,
                    "chargedCurrency": "ILS",
                    "description": "ישראכרט",
                    "status": "completed",
                }
            ]
        )
        commit(s, preview(s, root, card_file, "c.json", account, card, "isracard", {}).id)
        rows = {(t.merchant, t.card_id is None): t for t in s.scalars(select(Transaction))}
        settlement = rows[("ישראכרט", True)]
        assert settlement.kind == "card_settlement" and settlement.review_status == "confirmed"
        assert rows[("ישראכרט", False)].kind == "purchase"  # a card purchase, never a settlement
        assert rows[("משכורת", True)].review_status == "pending"  # no rule: stays in review


def test_rebilled_reference_matches_and_takes_new_charge_date(ledger):
    engine, root, account, card = ledger
    with session_scope(engine, True) as s:
        commit(
            s,
            preview(
                s,
                root,
                csv(["2026-09-03,2026-09-16,Refund,151.70,ILS,r1"], True),
                "a.csv",
                account,
                card,
                "isracard",
                {},
            ).id,
        )
        moved = preview(
            s,
            root,
            csv(["2026-09-03,2026-10-10,Refund,151.70,ILS,r1"], True),
            "b.csv",
            account,
            card,
            "isracard",
            {},
        )
        assert moved.summary["matched"] == 1 and moved.summary["new"] == 0
        commit(s, moved.id)
        transaction = s.scalar(select(Transaction))
        assert str(transaction.posting_date) == "2026-10-10"
        again = preview(
            s,
            root,
            csv(["2026-09-03,2026-10-10,Refund,151.70,ILS,r1", ""], True),
            "c.csv",
            account,
            card,
            "isracard",
            {},
        )
        assert again.summary["matched"] == 1
        assert s.scalar(select(func.count()).select_from(Transaction)) == 1


def test_fetcher_bill_date_does_not_override_off_cycle_charge(ledger):
    from test_parsers import isracard_fetcher_export, isracard_web_export
    from family_budget.models import CreditCard

    engine, root, account, card = ledger
    with session_scope(engine, True) as s:
        s.get(CreditCard, card).last_four = "1234"
        commit(s, preview(s, root, isracard_web_export(), "x.xlsx", account, card, "isracard", {}).id)
        before = {t.id: t.posting_date for t in s.scalars(select(Transaction))}
        moved = json.loads(isracard_fetcher_export())
        for txn in moved["txns"]:
            txn["processedDate"] = "2026-12-09T22:00:00.000Z"
        fetched = preview(s, root, json.dumps(moved).encode(), "x.json", account, card, "isracard", {})
        rows = list(s.scalars(select(ImportRow).where(ImportRow.batch_id == fetched.id)))
        for row in rows:
            if row.status == "ambiguous":
                resolve_row(s, row.id, "duplicate:" + row.candidates[0])
        commit(s, fetched.id)
        assert {t.id: t.posting_date for t in s.scalars(select(Transaction)) if t.id in before} == before
