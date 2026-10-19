from datetime import date

import pytest

pytest.importorskip("sqlalchemy")
from fastapi.testclient import TestClient
from sqlalchemy import select

from family_budget import periods
from family_budget.api.app import create_app
from family_budget.db import session_scope
from family_budget.models import AuditEvent
from family_budget.reporting.service import report, tx_query
from test_accounting import tx


def test_period_bounds_include_start_day_and_exclude_next():
    assert periods.bounds("2026-10", 10) == (date(2026, 10, 10), date(2026, 11, 10))
    assert periods.bounds("2026-12", 10) == (date(2026, 12, 10), date(2027, 1, 10))
    assert periods.bounds("2026-10", 1) == (date(2026, 10, 1), date(2026, 11, 1))
    assert periods.period_of(date(2026, 10, 10), 10) == "2026-10"
    assert periods.period_of(date(2026, 11, 9), 10) == "2026-10"
    assert periods.period_of(date(2026, 10, 9), 10) == "2026-09"
    assert periods.period_of(date(2026, 1, 5), 10) == "2025-12"
    for bad in (0, 29, True, "10"):
        with pytest.raises(ValueError):
            periods.validate_start_day(bad)


def test_report_and_transaction_filter_follow_period(ledger):
    engine, _, account, card = ledger
    with session_scope(engine, True) as s:
        periods.set_start_day(s, 10)
        before = tx(s, account, -1000, card=card, merchant="Before", when="2026-10-09", posting="2026-10-09")
        first = tx(
            s, account, -2000, card=card, merchant="First day", when="2026-10-10", posting="2026-10-10"
        )
        last = tx(s, account, -4000, card=card, merchant="Last day", when="2026-11-09", posting="2026-11-09")
        after = tx(
            s, account, -8000, card=card, merchant="Next period", when="2026-11-10", posting="2026-11-10"
        )
        october = report(s, "2026-10")
        assert (october["period_start"], october["period_end"]) == ("2026-10-10", "2026-11-09")
        assert october["consumption"] == "60.00" and october["transaction_count"] == 2
        ids = {t.id for t in s.scalars(tx_query("2026-10", start_day=10))}
        assert ids == {first.id, last.id}
        assert before.id in {t.id for t in s.scalars(tx_query("2026-09", start_day=10))}
        assert after.id in {t.id for t in s.scalars(tx_query("2026-11", start_day=10))}
        periods.set_start_day(s, 1)
        assert report(s, "2026-10")["consumption"] == "30.00"  # calendar October: the 9th and 10th only


def test_preferences_api_validates_and_audits(ledger):
    engine, root, *_ = ledger
    headers = {"X-Family-Budget": "local"}
    with TestClient(create_app(engine, root, initialize=False)) as client:
        assert client.get("/api/settings").json()["preferences"] == {
            "period_start_day": 1,
            "card_spending_basis": "purchase",
            "review_all_imports": False,
        }
        for bad in (0, 29, "10"):
            assert (
                client.put("/api/preferences", json={"period_start_day": bad}, headers=headers).status_code
                == 422
            )
        assert (
            client.put("/api/preferences", json={"period_start_day": 10}, headers=headers).status_code == 200
        )
        assert client.get("/api/settings").json()["preferences"]["period_start_day"] == 10
        dashboard = client.get("/api/dashboard?month=2026-10").json()
        assert (dashboard["period_start"], dashboard["period_end"]) == ("2026-10-10", "2026-11-09")
    with session_scope(engine) as s:
        event = s.scalar(select(AuditEvent).where(AuditEvent.entity_type == "app_settings"))
        assert (event.before, event.after) == ({"period_start_day": 1}, {"period_start_day": 10})


def test_charge_basis_counts_card_spending_by_the_bill_it_is_on(ledger):
    from family_budget.api.schemas import Correction
    from family_budget.classification.corrections import correct

    engine, _, account, card = ledger
    with session_scope(engine, True) as s:
        periods.set_start_day(s, 10)
        early = tx(
            s, account, -1000, card=card, merchant="Bought 08.10", when="2026-10-08", posting="2026-11-10"
        )
        late = tx(
            s, account, -2000, card=card, merchant="Bought 15.10", when="2026-10-15", posting="2026-11-10"
        )
        old_bill = tx(
            s,
            account,
            -4000,
            card=card,
            merchant="Installment on 10.10 bill",
            when="2026-07-15",
            posting="2026-10-10",
        )
        off_cycle = tx(
            s, account, -8000, card=card, merchant="Off-cycle 02.11", when="2026-09-12", posting="2026-11-02"
        )
        bank = tx(s, account, -16000, merchant="Bank purchase 20.10", when="2026-10-20", posting="2026-10-20")
        by_purchase = report(s, "2026-10")["consumption"]
        assert by_purchase == "180.00"  # bought in the period: 15.10 (20) + bank 20.10 (160)
        periods.set_card_basis(s, "charge")
        october = report(s, "2026-10")
        assert october["card_spending_basis"] == "charge"
        assert october["consumption"] == "270.00"  # 10.11 bill (10 + 20) + 02.11 off-cycle (80) + bank (160)
        ids = {t.id for t in s.scalars(tx_query("2026-10", start_day=10, basis="charge"))}
        assert ids == {early.id, late.id, off_cycle.id, bank.id}
        assert old_bill.id in {t.id for t in s.scalars(tx_query("2026-09", start_day=10, basis="charge"))}
        correct(
            s,
            Correction(
                id=late.id,
                version=late.version,
                category_id=late.category_id,
                scope="household",
                kind="purchase",
                allocation_date=date(2026, 12, 1),
                note="moved by hand",
                splits=[],
            ),
        )
        assert late.allocation_manual
        assert report(s, "2026-10")["consumption"] == "250.00"  # the hand-set date wins over the bill
        with pytest.raises(ValueError):
            periods.set_card_basis(s, "weekly")


def test_charges_split_by_bill_and_purpose_including_unreviewed(ledger):
    from family_budget.models import CreditCard, Project

    engine, _, account, card = ledger
    with session_scope(engine, True) as s:
        periods.set_start_day(s, 10)
        other = CreditCard(account_id=account, name="Second", last_four="9999")
        trip = Project(name="Trip", budget_minor=0, scope="project")
        s.add_all([other, trip])
        s.flush()
        tx(s, account, -1000, card=card, when="2026-10-01", posting="2026-11-10")
        tx(s, account, -2000, card=other.id, when="2026-10-15", posting="2026-11-10", confirmed=False)
        for day in (12, 13, 14):  # off-cycle trip charges on one card outnumber the bill rows
            tx(s, account, -500, card=card, when=f"2026-09-{day}", posting="2026-11-02", project_id=trip.id)
        tx(s, account, -300, merchant="Pub", scope="pub", when="2026-10-20", posting="2026-10-20")
        tx(s, account, -9999, card=card, when="2026-09-01", posting="2026-10-10")  # previous bill
        result = report(s, "2026-10")["charges"]
        assert result["total"] == "48.00"
        assert [(c["date"], c["kind"], c["total"]) for c in result["by_charge"]] == [
            ("2026-10-20", "bank", "3.00"),
            ("2026-11-02", "separate", "15.00"),
            ("2026-11-10", "bill", "30.00"),
        ]
        assert {p["label"]: p["total"] for p in result["by_purpose"]} == {
            "הוצאות שוטפות": "10.00",
            "Trip": "15.00",
            "פאב קהילתי": "3.00",
            "ממתין לאישור": "20.00",
        }
