from datetime import date

import pytest

pytest.importorskip("sqlalchemy")
from fastapi.testclient import TestClient
from sqlalchemy import select

from family_budget import periods
from family_budget.api.app import create_app
from family_budget.api.schemas import Correction
from family_budget.classification.corrections import correct
from family_budget.db import session_scope
from family_budget.models import Category, Project, Reimbursement
from family_budget.receivables import record_payment, summary
from family_budget.reporting.service import report
from test_accounting import tx

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def assign(session, t, project_id, **extra):
    category = session.scalar(select(Category.id).where(Category.code == "groceries"))
    values = dict(
        id=t.id, version=t.version, category_id=category, kind="purchase", note="community", splits=[]
    )
    return correct(session, Correction(**values, project_id=project_id, **extra))


def test_community_expenses_are_receivables_paid_back_by_one_or_more_payments(ledger):
    engine, _, account, card = ledger
    with session_scope(engine, True) as s:
        periods.set_start_day(s, 10)  # the household's financial month: 10.10-09.11 holds the 10.11 bill
        pub = Project(name="Community pub", budget_minor=0, scope="project", reimbursable=True)
        s.add(pub)
        s.flush()
        first = tx(s, account, -10000, card=card, merchant="Beer", when="2026-10-12", posting="2026-11-10")
        second = tx(s, account, -2500, card=card, merchant="Ice", when="2026-10-14", posting="2026-11-10")
        own = tx(
            s, account, -700, card=card, merchant="Own groceries", when="2026-10-15", posting="2026-11-10"
        )
        assign(s, first, pub.id)
        assign(s, second, pub.id)
        assert first.scope == "project" and s.scalar(
            select(Reimbursement).where(Reimbursement.expense_id == first.id)
        )
        october = report(s, "2026-10")
        assert october["consumption"] == "7.00"  # only the household's own purchase
        assert october["pending_reimbursements"] == "125.00"
        groups = {p["label"]: p["total"] for p in october["charges"]["by_purpose"]}
        assert groups["לגבייה: Community pub"] == "125.00" and october["charges"]["total"] == "132.00"

        with pytest.raises(ValueError, match="more than what this project still owes"):
            record_payment(s, pub.id, date(2026, 10, 20), "125.01", "bit", "", None, None)
        payment = record_payment(s, pub.id, date(2026, 10, 20), "110.00", "paybox", "", None, None)
        owed = summary(s, today=date(2026, 10, 30))[0]
        assert (owed["paid"], owed["received"], owed["outstanding"], owed["oldest_days"]) == (
            "125.00",
            "110.00",
            "15.00",
            16,
        )
        assert [i["outstanding"] for i in owed["items"]] == ["0.00", "15.00"]  # oldest receipt settled first
        assert report(s, "2026-10")["pending_reimbursements"] == "15.00"

        with pytest.raises(ValueError, match="already \\(partly\\) reimbursed"):
            assign(s, first, None, reimbursement_expected=False)
        from family_budget.receivables import delete_payment

        delete_payment(s, payment.id)
        assert summary(s)[0]["outstanding"] == "125.00"
        assign(s, first, None, reimbursement_expected=False)  # back to the household: no longer owed
        assert summary(s)[0]["outstanding"] == "25.00"
        assert own.id not in {r.expense_id for r in s.scalars(select(Reimbursement))}


def test_project_flag_receipts_and_backup(ledger, tmp_path):
    from family_budget.backup import backup, restore

    engine, root, account, card = ledger
    headers = {"X-Family-Budget": "local"}
    with TestClient(create_app(engine, root, initialize=False)) as client:
        imported = client.post(
            "/api/imports/preview",
            data={"account_id": account, "card_id": card, "adapter": "isracard", "options": "{}"},
            files={
                "file": (
                    "x.csv",
                    b"transaction_date,posting_date,merchant,amount,currency\n2026-10-12,2026-11-10,Sound system,-50.00,ILS\n",
                )
            },
            headers=headers,
        ).json()
        assert client.post(f"/api/imports/{imported['id']}/commit", headers=headers).status_code == 200
        expense_id = client.get("/api/transactions?limit=10").json()[0]["id"]
        project = client.post(
            "/api/projects", json={"name": "Festival", "budget": "0", "scope": "project"}, headers=headers
        ).json()
        tx_now = client.get("/api/transactions?limit=10").json()[0]
        category = client.get("/api/settings").json()["categories"][0]["id"]
        client.post(
            "/api/transactions/review",
            json={
                "items": [
                    {
                        "id": expense_id,
                        "version": tx_now["version"],
                        "category_id": category,
                        "project_id": project["id"],
                        "kind": "purchase",
                        "note": "festival",
                        "splits": [],
                    }
                ]
            },
            headers=headers,
        )
        assert client.get("/api/receivables").json() == []
        flagged = client.put(
            f"/api/projects/{project['id']}",
            json={"name": "Festival", "budget": "0", "scope": "project", "reimbursable": True},
            headers=headers,
        )
        assert flagged.status_code == 200
        assert client.get("/api/receivables").json()[0]["outstanding"] == "50.00"

        bad = client.post(
            f"/api/transactions/{expense_id}/attachments",
            files={"file": ("x.txt", b"hello")},
            headers=headers,
        )
        assert bad.status_code == 422
        added = client.post(
            f"/api/transactions/{expense_id}/attachments",
            files={"file": ("../../receipt.png", PNG)},
            headers=headers,
        ).json()
        assert added["filename"] == "receipt.png" and added["content_type"] == "image/png"
        download = client.get(f"/api/attachments/{added['id']}")
        assert download.content == PNG and download.headers["content-disposition"].startswith("attachment")
        assert client.get("/api/receivables").json()[0]["items"][0]["attachments"] == 1
        paid = client.post(
            "/api/receivables/payments",
            json={
                "project_id": project["id"],
                "received_on": "2026-10-25",
                "amount": "50.00",
                "method": "bank",
            },
            headers=headers,
        )
        assert paid.status_code == 201 and client.get("/api/receivables").json()[0]["outstanding"] == "0.00"
    archive = tmp_path / "backup.fbb"
    backup(root, archive, "a long test passphrase 123")
    restored = tmp_path / "restored"
    restore(archive, restored, "a long test passphrase 123")
    assert list((restored / "attachments").iterdir())[0].read_bytes() == PNG
