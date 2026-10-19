import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sqlalchemy")
from fastapi.testclient import TestClient
from family_budget.api.app import create_app


def test_api_end_to_end_and_privacy(ledger, fixture_dir):
    engine, root, account, card = ledger
    with TestClient(create_app(engine, root, initialize=False)) as client:
        assert client.get("/api/health").json()["ai_enabled"] is False
        assert client.get("/api/settings").status_code == 200
        assert (
            client.post("/api/accounts", json={"name": "No CSRF header", "institution": "test"}).status_code
            == 403
        )
        headers = {"X-Family-Budget": "local"}
        assert (
            client.post(
                "/api/accounts",
                headers={**headers, "Origin": "https://evil.example"},
                json={"name": "Attack", "institution": "test"},
            ).status_code
            == 403
        )
        assert client.get("/api/settings", headers={"Host": "evil.example"}).status_code == 400
        response = client.post(
            "/api/imports/preview",
            headers=headers,
            data={"account_id": account, "card_id": card, "adapter": "isracard"},
            files={
                "file": ("synthetic.csv", (fixture_dir / "isracard_canonical.csv").read_bytes(), "text/csv")
            },
        )
        assert response.status_code == 200, response.text
        batch_id = response.json()["id"]
        assert client.get("/api/transactions").json() == []
        assert client.post("/api/imports/" + batch_id + "/commit", headers=headers).status_code == 200
        txs = client.get("/api/transactions?month=2026-10").json()
        assert len(txs) == 2
        assert all(isinstance(t["amount"], str) for t in txs)
        dashboard = client.get("/api/dashboard?month=2026-10").json()
        assert dashboard["consumption"] == "600.00"
        assert dashboard["net_cash_flow"] == "0.00"
        assert client.get("/api/dashboard?month=invalid").status_code == 422
        assert client.get("/api/health").headers["Cache-Control"] == "no-store"


def test_bad_upload_and_atomic_review(ledger, fixture_dir):
    engine, root, account, card = ledger
    with TestClient(create_app(engine, root, initialize=False)) as client:
        headers = {"X-Family-Budget": "local"}
        result = client.post(
            "/api/imports/preview",
            headers=headers,
            data={"account_id": account, "adapter": "hapoalim"},
            files={"file": ("payload.exe", b"hello")},
        )
        assert result.status_code == 422
        assert client.post("/api/transactions/review", headers=headers, json={"items": []}).status_code == 422
        assert client.get("/api/imports/not-found").status_code == 404


def test_database_session_commits_before_response(ledger):
    # A "request"-scoped yield dependency commits after the response is sent: a client that refetches
    # straight after a save reads stale rows, and commit failures are hidden behind a success status.
    from fastapi.routing import APIRoute

    engine, root, *_ = ledger
    app = create_app(engine, root, initialize=False)
    sessions = [
        d
        for route in app.routes
        if isinstance(route, APIRoute)
        for d in route.dependant.dependencies
        if getattr(d.call, "__name__", "") == "db"
    ]
    assert len(sessions) > 30
    assert {d.scope for d in sessions} == {"function"}


def test_inbox_lists_and_previews_fetched_files_only(ledger, tmp_path, monkeypatch):
    from test_parsers import isracard_fetcher_export

    engine, root, account, card = ledger
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "isracard-1234-202610190800.json").write_bytes(isracard_fetcher_export())
    (inbox / "notes.txt").write_text("not offered")
    monkeypatch.setenv("FAMILY_BUDGET_INBOX", str(inbox))
    headers = {"X-Family-Budget": "local"}
    with TestClient(create_app(engine, root, initialize=False)) as client:
        listed = client.get("/api/inbox").json()
        assert listed == [
            {
                "name": "isracard-1234-202610190800.json",
                "source": "isracard",
                "card_last_four": "1234",
                "size": len(isracard_fetcher_export()),
                "status": None,
                "latest": True,
                "to_import": None,  # the test card has no last four digits, so the file has no target
            }
        ]
        body = {"account_id": account, "card_id": card}
        batch = client.post("/api/inbox/isracard-1234-202610190800.json/preview", json=body, headers=headers)
        assert batch.status_code == 200 and batch.json()["parser"] == "isracard:isracard_scraper_json_v1"
        assert client.get("/api/inbox").json()[0]["status"] == "preview"
        assert client.post(f"/api/imports/{batch.json()['id']}/commit", headers=headers).status_code == 200
        (inbox / "isracard-1234-202610200800.json").write_bytes(isracard_fetcher_export(fetched_at="later"))
        assert [(f["name"][-17:-5], f["status"], f["latest"]) for f in client.get("/api/inbox").json()] == [
            ("202610200800", None, True),
            ("202610190800", "committed", False),
        ]
        for name in ("notes.txt", "..%2Fbudget.sqlite3", "isracard-1234-202610190801.json"):
            # Traversal never reaches the handler (405); unknown names are 404. Neither reads a file.
            assert client.post(f"/api/inbox/{name}/preview", json=body, headers=headers).status_code in {
                404,
                405,
            }


def test_inbox_previews_bank_files_against_a_bank_account(ledger, tmp_path, monkeypatch):
    from test_parsers import hapoalim_fetcher_export

    engine, root, account, card = ledger
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    name = "hapoalim-3456-202610190800.json"
    (inbox / name).write_bytes(hapoalim_fetcher_export())
    monkeypatch.setenv("FAMILY_BUDGET_INBOX", str(inbox))
    headers = {"X-Family-Budget": "local"}
    with TestClient(create_app(engine, root, initialize=False)) as client:
        [listed] = client.get("/api/inbox").json()
        assert listed["source"] == "hapoalim" and listed["card_last_four"] == "3456"
        wrong = client.post(f"/api/inbox/{name}/preview", json={"account_id": account, "card_id": card}, headers=headers)
        assert wrong.status_code == 422
        batch = client.post(f"/api/inbox/{name}/preview", json={"account_id": account}, headers=headers)
        assert batch.status_code == 200 and batch.json()["parser"] == "hapoalim:hapoalim_scraper_json_v1"
        assert batch.json()["summary"]["native"]["balance"] == "1234.50"
        assert client.get("/api/inbox").json()[0]["to_import"] == 4
        assert client.post(f"/api/imports/{batch.json()['id']}/commit", headers=headers).status_code == 200
        # The next fetch overlaps the same days: nothing new, nothing ambiguous.
        later = "hapoalim-3456-202610200600.json"
        (inbox / later).write_bytes(hapoalim_fetcher_export(fetched_at="2026-10-20T03:00:00.000Z"))
        newest = client.get("/api/inbox").json()[0]
        assert newest["name"] == later and newest["to_import"] == 0
        again = client.post(f"/api/inbox/{later}/preview", json={"account_id": account}, headers=headers).json()
        assert again["summary"]["matched"] == 4 and again["summary"]["ambiguous"] == 0
        # A preview opened before this rule existed is rechecked when the file is opened again.
        from family_budget.db import session_scope
        from family_budget.models import ImportRow
        from sqlalchemy import select

        with session_scope(engine, True) as s:
            for row in s.scalars(select(ImportRow).where(ImportRow.batch_id == again["id"])):
                row.status = "ambiguous"
        reopened = client.post(f"/api/inbox/{later}/preview", json={"account_id": account}, headers=headers).json()
        assert reopened["id"] == again["id"] and reopened["summary"]["matched"] == 4
        rows = client.get(f"/api/imports/{again['id']}").json()["rows"]
        assert {r["status"] for r in rows} == {"matched"}


def test_cards_can_be_edited_and_unused_cards_deleted(ledger, fixture_dir):
    engine, root, account, card = ledger
    headers = {"X-Family-Budget": "local"}
    with TestClient(create_app(engine, root, initialize=False)) as client:
        spare = client.post(
            "/api/cards",
            json={"account_id": account, "name": "Duplicate", "last_four": "5678"},
            headers=headers,
        ).json()
        clash = client.put(f"/api/cards/{card}", json={"name": "Main", "last_four": "5678"}, headers=headers)
        assert clash.status_code == 422 and "already ends in 5678" in clash.json()["detail"]
        assert (
            client.post(
                "/api/cards",
                json={"account_id": account, "name": "Third", "last_four": "5678"},
                headers=headers,
            ).status_code
            == 422
        )
        assert client.delete(f"/api/cards/{spare['id']}", headers=headers).status_code == 200
        edited = client.put(f"/api/cards/{card}", json={"name": "Main", "last_four": "5678"}, headers=headers)
        assert edited.status_code == 200 and edited.json()["last_four"] == "5678"
        upload = client.post(
            "/api/imports/preview",
            data={"account_id": account, "card_id": card, "adapter": "isracard", "options": "{}"},
            files={"file": ("x.csv", (fixture_dir / "isracard_canonical.csv").read_bytes())},
            headers=headers,
        )
        assert upload.status_code == 200
        in_use = client.delete(f"/api/cards/{card}", headers=headers)
        assert in_use.status_code == 422 and "cannot be deleted" in in_use.json()["detail"]
        assert [c["name"] for c in client.get("/api/settings").json()["cards"]] == ["Main"]


def test_fetcher_status_and_run_are_proxied_without_credentials(ledger, monkeypatch):
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    calls = []

    class Fetcher(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            calls.append(("GET", self.path))
            body = json.dumps(
                {"running": False, "next_run": "2026-10-20T03:00:00.000Z", "last": None}
            ).encode()
            self.send_response(200)
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            calls.append(("POST", self.path, self.rfile.read(length).decode()))
            if self.path == "/code":
                self.send_response(202)
                self.end_headers()
                self.wfile.write(b'{"accepted": true}')
                return
            self.send_response(409 if len(calls) > 2 else 202)
            self.end_headers()
            self.wfile.write(b'{"started": true}')

    server = HTTPServer(("127.0.0.1", 0), Fetcher)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    engine, root, *_ = ledger
    headers = {"X-Family-Budget": "local"}
    try:
        monkeypatch.setenv("FAMILY_BUDGET_FETCHER_URL", f"http://127.0.0.1:{server.server_port}")
        with TestClient(create_app(engine, root, initialize=False)) as client:
            status = client.get("/api/fetcher/status").json()
            assert status["available"] and status["next_run"] == "2026-10-20T03:00:00.000Z"
            assert client.post("/api/fetcher/run", headers=headers).status_code == 202
            assert client.post("/api/fetcher/run", headers=headers).status_code == 409
            assert client.post("/api/fetcher/run").status_code == 403  # mutation without the local header
            for bad in ("12", "12a456", "123456789", 123456):
                assert client.post("/api/fetcher/code", json={"code": bad}, headers=headers).status_code == 422
            assert client.post("/api/fetcher/code", json={"code": "123456"}, headers=headers).status_code == 202
        assert calls == [
            ("GET", "/status"),
            ("POST", "/run", ""),
            ("POST", "/run", ""),
            ("POST", "/code", '{"code": "123456"}'),
        ]
    finally:
        server.shutdown()
    monkeypatch.setenv("FAMILY_BUDGET_FETCHER_URL", "http://127.0.0.1:9")
    with TestClient(create_app(engine, root, initialize=False)) as client:
        assert client.get("/api/fetcher/status").json()["available"] is False
        assert client.post("/api/fetcher/run", headers=headers).status_code == 503


def test_recurring_expenses_can_be_edited_and_deleted(ledger):
    engine, root, *_ = ledger
    headers = {"X-Family-Budget": "local"}
    with TestClient(create_app(engine, root, initialize=False)) as client:
        category = client.get("/api/settings").json()["categories"][0]["id"]
        body = {"name": "ועד בית", "category_id": category, "expected": "120", "day_of_month": 5}
        item = client.post("/api/recurring", json=body, headers=headers).json()
        edited = client.put(
            f"/api/recurring/{item['id']}", json=body | {"expected": "150", "active": False}, headers=headers
        )
        assert edited.status_code == 200
        [saved] = [r for r in client.get("/api/settings").json()["recurring"] if r["id"] == item["id"]]
        assert saved["expected"] == "150.00" and saved["active"] is False
        assert client.delete(f"/api/recurring/{item['id']}").status_code == 403  # needs the local header
        assert client.delete(f"/api/recurring/{item['id']}", headers=headers).status_code == 200
        assert item["id"] not in {r["id"] for r in client.get("/api/settings").json()["recurring"]}
        assert client.delete(f"/api/recurring/{item['id']}", headers=headers).status_code == 404


def test_budget_can_become_the_template_and_fund_targets_follow_the_plan(ledger):
    engine, root, *_ = ledger
    headers = {"X-Family-Budget": "local"}
    with TestClient(create_app(engine, root, initialize=False)) as client:
        october = client.get("/api/dashboard?month=2026-10").json()
        fund = october["funds"][0]
        allocations = {c["id"]: c["budget"] for c in october["categories"]}
        allocations[fund["category_id"]] = "2500.00"
        body = {"income": "32000", "allocations": allocations, "provisional": True}
        assert client.put("/api/budgets/2026-10", json=body, headers=headers).status_code == 200
        october = client.get("/api/dashboard?month=2026-10").json()
        assert next(f for f in october["funds"] if f["id"] == fund["id"])["monthly_target"] == "2500.00"
        november = client.get("/api/dashboard?month=2026-11").json()  # still the old template
        assert next(f for f in november["funds"] if f["id"] == fund["id"])["monthly_target"] == fund["monthly_target"]
        body["as_template"] = True
        assert client.put("/api/budgets/2026-10", json=body, headers=headers).status_code == 200
        november = client.get("/api/dashboard?month=2026-11").json()
        assert next(f for f in november["funds"] if f["id"] == fund["id"])["monthly_target"] == "2500.00"
        stored = next(f for f in client.get("/api/settings").json()["funds"] if f["id"] == fund["id"])
        assert stored["monthly_target"] == "2500.00"
