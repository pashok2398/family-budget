import hashlib
import json
import os
import re
import urllib.error
import urllib.request
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from uuid import uuid4
from fastapi import FastAPI, UploadFile, File, Form, Request, Depends, HTTPException
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from ..config import INBOX_FILE, MAX_UPLOAD, data_dir, fetcher_url, inbox_dir
from ..db import make_engine, session_scope
from ..models import (
    Attachment,
    Account,
    CreditCard,
    Category,
    Transaction,
    TransactionSplit,
    ImportBatch,
    ImportRow,
    Project,
    IncomeSource,
    SinkingFund,
    SourceDocument,
    Loan,
    LoanPayment,
    RecurringExpense,
    Reimbursement,
    TransactionMatch,
    AuditEvent,
    ClassificationRule,
    ClassificationDecision,
)
from ..ingestion import service as ingestion
from .. import attachments, periods, receivables
from ..classification.corrections import correct
from ..reconciliation import engine as reconciliation
from ..budgeting import service as budgeting
from ..reporting import service as reporting
from ..reporting import recurring as fixed_expenses
from ..normalization.money import money, minor
from .schemas import (
    AccountInput,
    CardInput,
    CardUpdateInput,
    Corrections,
    ResolveInput,
    MatchInput,
    BudgetInput,
    CategoryInput,
    FundInput,
    MovementInput,
    ProjectInput,
    ReimbursementPaymentInput,
    IncomeInput,
    LoanInput,
    LoanPaymentInput,
    InboxPreviewInput,
    FetcherCodeInput,
    PreferencesInput,
    RecurringInput,
)


def serialize(obj):
    result = {}
    for column in obj.__table__.columns:
        value = getattr(obj, column.name)
        if column.name.endswith("_minor"):
            result[column.name.removesuffix("_minor")] = money(value) if value is not None else None
        elif isinstance(value, date):
            result[column.name] = value.isoformat()
        else:
            result[column.name] = value
    if isinstance(obj, ImportRow):
        result["data"] = {
            key.removesuffix("_minor") if key.endswith("_minor") else key: money(value)
            if key.endswith("_minor") and value is not None
            else value
            for key, value in obj.data.items()
        }
    if isinstance(obj, ImportBatch):
        result["summary"] = dict(obj.summary)
        value = result["summary"].pop("extracted_total_minor", None)
        result["summary"]["extracted_total"] = money(value) if value is not None else None
    return result


def create_app(engine=None, root=None, initialize=True):
    root = Path(root) if root else data_dir()
    engine = engine or make_engine(root / "budget.sqlite3")

    @asynccontextmanager
    async def lifespan(app):
        if initialize:
            from ..migrate import upgrade
            from ..seed import seed

            upgrade(engine)
            with session_scope(engine, write=True) as session:
                seed(session)
        yield

    app = FastAPI(
        title="Family Budget",
        version="0.1.0",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    from .security import BodyLimitMiddleware

    app.add_middleware(BodyLimitMiddleware)
    app.state.engine, app.state.data_root = engine, root
    from ..config import allowed_hosts

    app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts())

    @app.middleware("http")
    async def local_security(request: Request, call_next):
        origin = request.headers.get("origin")
        if origin and origin != str(request.base_url).rstrip("/"):
            return JSONResponse({"detail": "Cross-origin requests are not allowed"}, status_code=403)
        if (
            request.method not in {"GET", "HEAD", "OPTIONS"}
            and request.headers.get("x-family-budget") != "local"
        ):
            return JSONResponse({"detail": "Missing local request header"}, status_code=403)
        try:
            size = int(request.headers.get("content-length", "0"))
        except ValueError:
            return JSONResponse({"detail": "Invalid content length"}, status_code=400)
        if size > MAX_UPLOAD + 65536:
            return JSONResponse({"detail": "Upload exceeds 20 MiB"}, status_code=413)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; script-src 'self'; connect-src 'self'; frame-ancestors 'none'"
        )
        return response

    @app.exception_handler(ValueError)
    async def invalid(request, error):
        return JSONResponse(
            {"detail": str(error)}, status_code=409 if isinstance(error, reconciliation.Conflict) else 422
        )

    @app.exception_handler(IntegrityError)
    async def integrity(request, error):
        return JSONResponse(
            {"detail": "This change conflicts with an existing record or linked data"}, status_code=409
        )

    # Use as Depends(db, scope="function"): the default "request" scope commits only after the response
    # is sent, so a client refetch can read pre-commit data and commit errors never reach the client.
    def db(request: Request):
        with session_scope(engine, write=request.method not in {"GET", "HEAD"}) as session:
            yield session

    def get_or_error(session, model, key):
        item = session.get(model, key)
        if not item:
            raise HTTPException(404, "Record not found")
        return item

    @app.get("/api/health")
    def health():
        return {"status": "ok", "local_only": True, "ai_enabled": False}

    @app.get("/api/settings")
    def settings(session=Depends(db, scope="function")):
        return {
            "preferences": current_preferences(session),
        } | {
            name: [serialize(x) for x in session.scalars(select(model))]
            for name, model in [
                ("accounts", Account),
                ("cards", CreditCard),
                ("categories", Category),
                ("projects", Project),
                ("income_sources", IncomeSource),
                ("funds", SinkingFund),
                ("loans", Loan),
                ("recurring", RecurringExpense),
            ]
        }

    def current_preferences(session):
        return {
            "period_start_day": periods.start_day(session),
            "card_spending_basis": periods.card_basis(session),
            "review_all_imports": periods.review_all(session),
        }

    @app.put("/api/preferences")
    def preferences(body: PreferencesInput, session=Depends(db, scope="function")):
        changes = body.model_dump(exclude_none=True)
        before = current_preferences(session)
        if "period_start_day" in changes:
            periods.set_start_day(session, changes["period_start_day"])
        if "card_spending_basis" in changes:
            periods.set_card_basis(session, changes["card_spending_basis"])
        if "review_all_imports" in changes:
            periods.set_review_all(session, changes["review_all_imports"])
        session.add(
            AuditEvent(
                entity_type="app_settings",
                entity_id="preferences",
                action="edit",
                before={k: before[k] for k in changes},
                after=changes,
            )
        )
        return current_preferences(session)

    @app.post("/api/accounts", status_code=201)
    def account(body: AccountInput, session=Depends(db, scope="function")):
        item = Account(**body.model_dump())
        session.add(item)
        session.flush()
        return serialize(item)

    def unique_last_four(session, last_four, card_id=None):
        # Fetched and exported Isracard files are routed to cards by their last four digits.
        if last_four and session.scalar(
            select(CreditCard.id).where(CreditCard.last_four == last_four, CreditCard.id != (card_id or ""))
        ):
            raise ValueError(f"Another card already ends in {last_four}")

    @app.post("/api/cards", status_code=201)
    def card(body: CardInput, session=Depends(db, scope="function")):
        get_or_error(session, Account, body.account_id)
        unique_last_four(session, body.last_four)
        item = CreditCard(**body.model_dump())
        session.add(item)
        session.flush()
        return serialize(item)

    @app.put("/api/cards/{card_id}")
    def update_card(card_id: str, body: CardUpdateInput, session=Depends(db, scope="function")):
        item = get_or_error(session, CreditCard, card_id)
        unique_last_four(session, body.last_four, card_id)
        before = serialize(item)
        item.name, item.last_four = body.name, body.last_four
        session.add(
            AuditEvent(
                entity_type="credit_cards",
                entity_id=item.id,
                action="edit",
                before=before,
                after=serialize(item),
            )
        )
        return serialize(item)

    @app.delete("/api/cards/{card_id}")
    def delete_card(card_id: str, session=Depends(db, scope="function")):
        item = get_or_error(session, CreditCard, card_id)
        for model in (Transaction, ImportBatch):
            if session.scalar(select(func.count()).select_from(model).where(model.card_id == card_id)):
                raise ValueError("This card has transactions or imports and cannot be deleted")
        session.add(
            AuditEvent(
                entity_type="credit_cards",
                entity_id=item.id,
                action="delete",
                before=serialize(item),
                after={},
            )
        )
        session.delete(item)
        return {"deleted": card_id}

    @app.get("/api/imports")
    def imports(session=Depends(db, scope="function")):
        return [
            serialize(x) for x in session.scalars(select(ImportBatch).order_by(ImportBatch.created_at.desc()))
        ]

    @app.post("/api/imports/preview")
    def preview(
        file: UploadFile = File(...),
        account_id: str = Form(...),
        card_id: str = Form(""),
        adapter: str = Form(...),
        options: str = Form("{}"),
        session=Depends(db, scope="function"),
    ):
        try:
            parsed_options = json.loads(options)
        except ValueError:
            raise ValueError("Options must be a JSON object") from None
        if not isinstance(parsed_options, dict):
            raise ValueError("Options must be an object")
        content = file.file.read(MAX_UPLOAD + 1)
        return serialize(
            ingestion.preview(
                session,
                root,
                content,
                file.filename or "",
                account_id,
                card_id or None,
                adapter,
                parsed_options,
            )
        )

    def inbox_file(name: str) -> Path:
        folder = inbox_dir()
        if not folder or not re.fullmatch(INBOX_FILE, name) or not (folder / name).is_file():
            raise HTTPException(404, "Fetched file not found")
        return folder / name

    def call_fetcher(method, path, body=None):
        base = fetcher_url()
        if not base:
            return 503, {"available": False, "reason": "not configured"}
        request = urllib.request.Request(
            base.rstrip("/") + path,
            method=method,
            data=json.dumps(body).encode() if body is not None else b"" if method == "POST" else None,
            headers={"Content-Type": "application/json"} if body is not None else {},
        )
        try:
            with urllib.request.urlopen(request, timeout=3) as response:
                return response.status, json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read() or b"{}")
        except (OSError, ValueError):
            return 503, {"available": False, "reason": "not running"}

    @app.get("/api/fetcher/status")
    def fetcher_status():
        status, body = call_fetcher("GET", "/status")
        return {"available": status == 200, **body}

    @app.post("/api/fetcher/run", status_code=202)
    def fetcher_run():
        status, body = call_fetcher("POST", "/run")
        if status == 409:
            raise HTTPException(409, "A fetch is already running")
        if status != 202:
            raise HTTPException(503, "The fetcher is not running")
        return body

    @app.post("/api/fetcher/code", status_code=202)
    def fetcher_code(body: FetcherCodeInput):
        # The one-time SMS code goes straight to the waiting bank login; it is not stored or logged.
        status, reply = call_fetcher("POST", "/code", {"code": body.code})
        if status == 409:
            raise HTTPException(409, "No bank login is waiting for a code; start a fetch first")
        if status != 202:
            raise HTTPException(
                503 if status >= 500 else 422, reply.get("reason") or "The code was not accepted"
            )
        return reply

    @app.get("/api/inbox")
    def inbox(session=Depends(db, scope="function")):
        folder = inbox_dir()
        if not folder or not folder.is_dir():
            return []
        files = sorted(
            (p for p in folder.iterdir() if re.fullmatch(INBOX_FILE, p.name) and p.is_file()),
            key=lambda p: p.name.rsplit("-", 1)[1],  # fetch time, newest first
            reverse=True,
        )[:50]
        newest = {}
        result = []
        cards = {c.last_four: c for c in session.scalars(select(CreditCard)) if c.last_four}
        banks = [
            a
            for a in session.scalars(select(Account))
            if a.kind == "bank" and "hapoalim" in a.institution.lower()
        ]
        for p in files:
            source, last_four = p.name.split("-")[:2]
            content = p.read_bytes()
            sha = hashlib.sha256(content).hexdigest()
            statuses = set(
                session.scalars(
                    select(ImportBatch.status)
                    .join(SourceDocument, SourceDocument.id == ImportBatch.document_id)
                    .where(SourceDocument.sha256 == sha)
                )
            )
            latest = newest.setdefault((source, last_four), p.name) == p.name
            # Rows the file would add or ask about; null when its card or account is not set up.
            target = (
                (cards[last_four].account_id, cards[last_four].id)
                if source == "isracard" and last_four in cards
                else (banks[0].id, None)
                if source == "hapoalim" and len(banks) == 1
                else None
            )
            to_import = None
            if target and latest and "committed" not in statuses:
                try:
                    counts = ingestion.pending_rows(session, content, p.name, *target, source)
                    to_import = counts["new"] + counts["ambiguous"] + counts["rejected"]
                except ValueError:
                    to_import = None
            result.append(
                {
                    "name": p.name,
                    "source": source,
                    "card_last_four": last_four,
                    "size": p.stat().st_size,
                    # committed: already imported; preview: opened but not confirmed; null: never opened
                    "status": "committed" if "committed" in statuses else "preview" if statuses else None,
                    "latest": latest,
                    "to_import": to_import,
                }
            )
        return result

    @app.post("/api/inbox/{name}/preview")
    def inbox_preview(name: str, body: InboxPreviewInput, session=Depends(db, scope="function")):
        path = inbox_file(name)
        if path.stat().st_size > MAX_UPLOAD:
            raise ValueError("File exceeds 20 MiB")
        source = name.split("-")[0]
        if (source == "isracard") != bool(body.card_id):
            raise ValueError("Isracard files need a card; bank files need a bank account and no card")
        batch = ingestion.preview(
            session, root, path.read_bytes(), name, body.account_id, body.card_id, source, {}
        )
        if batch.status == "preview":  # reopened: recheck against what was imported since
            ingestion.refresh(session, batch.id)
        return serialize(batch)

    @app.get("/api/imports/{batch_id}")
    def batch(batch_id: str, session=Depends(db, scope="function")):
        item = get_or_error(session, ImportBatch, batch_id)
        rows = list(
            session.scalars(
                select(ImportRow).where(ImportRow.batch_id == batch_id).order_by(ImportRow.row_number)
            )
        )
        candidate_ids = {tx_id for row in rows for tx_id in row.candidates}
        candidates = {
            t.id: serialize(t)
            for t in session.scalars(select(Transaction).where(Transaction.id.in_(candidate_ids)))
        }
        return {
            **serialize(item),
            "rows": [
                {
                    **serialize(row),
                    "candidate_details": [candidates[x] for x in row.candidates if x in candidates],
                }
                for row in rows
            ],
        }

    @app.get("/api/imports/{batch_id}/document")
    def document(batch_id: str, session=Depends(db, scope="function")):
        from ..models import SourceDocument

        batch = get_or_error(session, ImportBatch, batch_id)
        doc = get_or_error(session, SourceDocument, batch.document_id)
        return FileResponse(
            root / "imports" / f"{doc.sha256}{doc.extension}",
            media_type="application/octet-stream",
            filename=f"statement-{doc.sha256[:12]}{doc.extension}",
        )

    @app.patch("/api/import-rows/{row_id}")
    def resolve(row_id: str, body: ResolveInput, session=Depends(db, scope="function")):
        return serialize(ingestion.resolve_row(session, row_id, body.resolution))

    @app.post("/api/imports/{batch_id}/refresh")
    def refresh(batch_id: str, session=Depends(db, scope="function")):
        return serialize(ingestion.refresh(session, batch_id))

    @app.post("/api/imports/{batch_id}/commit")
    def commit(batch_id: str, session=Depends(db, scope="function")):
        return serialize(ingestion.commit(session, batch_id))

    @app.get("/api/transactions")
    def transactions(
        month: str | None = None,
        account_id: str | None = None,
        card_id: str | None = None,
        category_id: str | None = None,
        project_id: str | None = None,
        scope: str | None = None,
        review_status: str | None = None,
        offset: int = 0,
        limit: int = 100,
        session=Depends(db, scope="function"),
    ):
        query = reporting.tx_query(
            month,
            account_id,
            card_id,
            category_id,
            project_id,
            scope,
            review_status,
            periods.start_day(session),
            periods.card_basis(session),
        )
        rows = list(
            session.scalars(
                query.order_by(Transaction.transaction_date.desc(), Transaction.id)
                .offset(max(0, offset))
                .limit(max(1, min(500, limit)))
            )
        )
        return [
            {
                **serialize(t),
                "reimbursement_expected": session.scalar(
                    select(Reimbursement.id).where(Reimbursement.expense_id == t.id)
                )
                is not None,
                "splits": [
                    serialize(s)
                    for s in session.scalars(
                        select(TransactionSplit).where(TransactionSplit.transaction_id == t.id)
                    )
                ],
            }
            for t in rows
        ]

    @app.post("/api/transactions/review")
    def review(body: Corrections, session=Depends(db, scope="function")):
        if len({x.id for x in body.items}) != len(body.items):
            raise ValueError("Duplicate transaction in review batch")
        return [serialize(correct(session, item)) for item in body.items]

    @app.get("/api/transactions/{tx_id}/history")
    def history(tx_id: str, session=Depends(db, scope="function")):
        get_or_error(session, Transaction, tx_id)
        return {
            "audit": [
                serialize(x)
                for x in session.scalars(
                    select(AuditEvent).where(AuditEvent.entity_id == tx_id).order_by(AuditEvent.created_at)
                )
            ],
            "decisions": [
                serialize(x)
                for x in session.scalars(
                    select(ClassificationDecision)
                    .where(ClassificationDecision.transaction_id == tx_id)
                    .order_by(ClassificationDecision.created_at)
                )
            ],
        }

    @app.get("/api/reconciliation")
    def matches(session=Depends(db, scope="function")):
        return {
            "suggestions": reconciliation.suggestions(session),
            "matches": [serialize(x) for x in session.scalars(select(TransactionMatch))],
            "reimbursements": [serialize(x) for x in session.scalars(select(Reimbursement))],
        }

    @app.post("/api/reconciliation")
    def reconcile(body: MatchInput, session=Depends(db, scope="function")):
        return serialize(reconciliation.reconcile(session, body.kind, body.ids, body.explanation))

    @app.post("/api/reconciliation/{match_id}/undo")
    def undo(match_id: str, session=Depends(db, scope="function")):
        return serialize(reconciliation.undo(session, match_id))

    @app.get("/api/dashboard")
    def dashboard(
        month: str,
        account_id: str | None = None,
        card_id: str | None = None,
        category_id: str | None = None,
        project_id: str | None = None,
        scope: str | None = None,
        review_status: str | None = None,
        session=Depends(db, scope="function"),
    ):
        filters = dict(
            account_id=account_id,
            card_id=card_id,
            category_id=category_id,
            project_id=project_id,
            scope=scope,
            review_status=review_status,
        )
        return {
            **reporting.report(session, month, **filters),
            "trends": reporting.trends(session, month),
            "adjustments": reporting.adjustments(session, month),
        }

    @app.put("/api/budgets/{month}")
    def budget(month: str, body: BudgetInput, session=Depends(db, scope="function")):
        return serialize(
            budgeting.update_budget(
                session, month, body.income, body.allocations, body.provisional, body.as_template
            )
        )

    def audited_update(session, item, values):
        before = serialize(item) if item.id else {}
        for key, value in values.items():
            setattr(item, key, value)
        session.add(item)
        session.flush()
        session.add(
            AuditEvent(
                entity_type=item.__tablename__,
                entity_id=item.id,
                action="save",
                before=before,
                after=serialize(item),
            )
        )
        return serialize(item)

    @app.post("/api/categories")
    def add_category(body: CategoryInput, session=Depends(db, scope="function")):
        return audited_update(session, Category(code=f"user-{uuid4()}"), body.model_dump())

    @app.put("/api/categories/{key}")
    def edit_category(key: str, body: CategoryInput, session=Depends(db, scope="function")):
        return audited_update(session, get_or_error(session, Category, key), body.model_dump())

    @app.post("/api/projects")
    def project(body: ProjectInput, session=Depends(db, scope="function")):
        if minor(body.budget) < 0:
            raise ValueError("Negative project budget")
        return audited_update(
            session,
            Project(),
            {
                "name": body.name,
                "budget_minor": minor(body.budget),
                "scope": body.scope,
                "active": body.active,
                "reimbursable": body.reimbursable,
            },
        )

    @app.put("/api/projects/{key}")
    def edit_project(key: str, body: ProjectInput, session=Depends(db, scope="function")):
        if minor(body.budget) < 0:
            raise ValueError("Negative project budget")
        item = get_or_error(session, Project, key)
        receivables.set_project_reimbursable(session, item, body.reimbursable)
        return audited_update(
            session,
            item,
            {
                "name": body.name,
                "budget_minor": minor(body.budget),
                "scope": body.scope,
                "active": body.active,
            },
        )

    @app.get("/api/receivables")
    def receivable_summary(session=Depends(db, scope="function")):
        return receivables.summary(session)

    @app.post("/api/receivables/payments", status_code=201)
    def receivable_payment(body: ReimbursementPaymentInput, session=Depends(db, scope="function")):
        payment = receivables.record_payment(
            session,
            body.project_id,
            body.received_on,
            body.amount,
            body.method,
            body.note,
            body.reimbursement_ids,
            body.transaction_id,
        )
        return serialize(payment)

    @app.delete("/api/receivables/payments/{payment_id}")
    def delete_receivable_payment(payment_id: str, session=Depends(db, scope="function")):
        receivables.delete_payment(session, payment_id)
        return {"deleted": payment_id}

    @app.get("/api/transactions/{tx_id}/attachments")
    def list_attachments(tx_id: str, session=Depends(db, scope="function")):
        get_or_error(session, Transaction, tx_id)
        return [
            serialize(a)
            for a in session.scalars(
                select(Attachment).where(Attachment.transaction_id == tx_id).order_by(Attachment.created_at)
            )
        ]

    @app.post("/api/transactions/{tx_id}/attachments", status_code=201)
    def add_attachment(tx_id: str, file: UploadFile = File(...), session=Depends(db, scope="function")):
        get_or_error(session, Transaction, tx_id)
        content = file.file.read(attachments.MAX_ATTACHMENT + 1)
        sha, content_type = attachments.store(root, content)
        item = Attachment(
            transaction_id=tx_id,
            filename=attachments.safe_name(file.filename),
            sha256=sha,
            content_type=content_type,
            size=len(content),
        )
        session.add(item)
        session.flush()
        session.add(
            AuditEvent(
                entity_type="attachments", entity_id=item.id, action="add", before={}, after=serialize(item)
            )
        )
        return serialize(item)

    @app.get("/api/attachments/{attachment_id}")
    def download_attachment(attachment_id: str, session=Depends(db, scope="function")):
        item = get_or_error(session, Attachment, attachment_id)
        return FileResponse(
            attachments.path_for(root, item.sha256, item.content_type),
            media_type=item.content_type,
            filename=item.filename,  # served as a download, never rendered inside the app
        )

    @app.delete("/api/attachments/{attachment_id}")
    def delete_attachment(attachment_id: str, session=Depends(db, scope="function")):
        item = get_or_error(session, Attachment, attachment_id)
        session.add(
            AuditEvent(
                entity_type="attachments",
                entity_id=item.id,
                action="delete",
                before=serialize(item),
                after={},
            )
        )
        session.delete(item)  # the file stays: it may be shared by hash and remains in backups history
        return {"deleted": attachment_id}

    @app.post("/api/funds")
    def fund(body: FundInput, session=Depends(db, scope="function")):
        get_or_error(session, Category, body.category_id)
        if minor(body.monthly_target) < 0:
            raise ValueError("Negative fund target")
        return audited_update(
            session,
            SinkingFund(),
            {
                "name": body.name,
                "category_id": body.category_id,
                "monthly_target_minor": minor(body.monthly_target),
            },
        )

    @app.post("/api/funds/{key}/movements")
    def movement(key: str, body: MovementInput, session=Depends(db, scope="function")):
        return serialize(
            budgeting.move_fund(
                session, key, body.amount, body.date, body.note, body.request_key, body.transaction_id
            )
        )

    @app.get("/api/funds/{key}/movements")
    def movements(key: str, session=Depends(db, scope="function")):
        from ..models import FundMovement

        return [
            serialize(x)
            for x in session.scalars(
                select(FundMovement).where(FundMovement.fund_id == key).order_by(FundMovement.date)
            )
        ]

    @app.put("/api/income-sources/{key}")
    def income(key: str, body: IncomeInput, session=Depends(db, scope="function")):
        if body.expected is not None and minor(body.expected) < 0:
            raise ValueError("Negative income plan")
        return audited_update(
            session,
            get_or_error(session, IncomeSource, key),
            {
                "name": body.name,
                "expected_minor": minor(body.expected) if body.expected is not None else None,
                "provisional": body.provisional,
            },
        )

    @app.post("/api/income-sources")
    def add_income(body: IncomeInput, session=Depends(db, scope="function")):
        if body.expected is not None and minor(body.expected) < 0:
            raise ValueError("Negative income plan")
        return audited_update(
            session,
            IncomeSource(),
            {
                "name": body.name,
                "expected_minor": minor(body.expected) if body.expected is not None else None,
                "provisional": body.provisional,
            },
        )

    @app.put("/api/loans/{key}")
    def loan(key: str, body: LoanInput, session=Depends(db, scope="function")):
        if minor(body.monthly_payment) < 0 or (body.principal is not None and minor(body.principal) < 0):
            raise ValueError("Negative loan amount")
        return audited_update(
            session,
            get_or_error(session, Loan, key),
            {
                "name": body.name,
                "principal_minor": minor(body.principal) if body.principal is not None else None,
                "monthly_payment_minor": minor(body.monthly_payment),
            },
        )

    @app.post("/api/loans")
    def add_loan(body: LoanInput, session=Depends(db, scope="function")):
        if minor(body.monthly_payment) < 0 or (body.principal is not None and minor(body.principal) < 0):
            raise ValueError("Negative loan amount")
        return audited_update(
            session,
            Loan(),
            {
                "name": body.name,
                "principal_minor": minor(body.principal) if body.principal is not None else None,
                "monthly_payment_minor": minor(body.monthly_payment),
            },
        )

    @app.get("/api/loans/{key}/payments")
    def loan_payments(key: str, session=Depends(db, scope="function")):
        get_or_error(session, Loan, key)
        return [
            serialize(item) for item in session.scalars(select(LoanPayment).where(LoanPayment.loan_id == key))
        ]

    def save_loan_payment(session, key, body, item):
        get_or_error(session, Loan, key)
        tx = get_or_error(session, Transaction, body.transaction_id)
        principal, interest = minor(body.principal), minor(body.interest)
        if (
            tx.kind not in {"mortgage", "loan_payment"}
            or tx.currency != "ILS"
            or tx.review_status != "confirmed"
            or min(principal, interest) < 0
            or principal + interest != -tx.amount_minor
        ):
            raise ValueError("Principal + interest must equal a confirmed ILS loan debit")
        return audited_update(
            session,
            item,
            {
                "loan_id": key,
                "transaction_id": tx.id,
                "principal_minor": principal,
                "interest_minor": interest,
            },
        )

    @app.post("/api/loans/{key}/payments")
    def loan_payment(key: str, body: LoanPaymentInput, session=Depends(db, scope="function")):
        return save_loan_payment(session, key, body, LoanPayment())

    @app.put("/api/loans/{key}/payments/{payment_id}")
    def edit_loan_payment(
        key: str, payment_id: str, body: LoanPaymentInput, session=Depends(db, scope="function")
    ):
        item = get_or_error(session, LoanPayment, payment_id)
        if item.loan_id != key:
            raise ValueError("Payment belongs to another loan")
        return save_loan_payment(session, key, body, item)

    @app.get("/api/recurring/suggestions")
    def recurring_suggestions(session=Depends(db, scope="function")):
        return fixed_expenses.suggestions(session)

    @app.post("/api/recurring")
    def recurring(body: RecurringInput, session=Depends(db, scope="function")):
        get_or_error(session, Category, body.category_id)
        if minor(body.expected) < 0:
            raise ValueError("Negative recurring amount")
        values = body.model_dump(exclude={"expected"})
        values["expected_minor"] = minor(body.expected)
        return audited_update(session, RecurringExpense(), values)

    @app.put("/api/recurring/{key}")
    def edit_recurring(key: str, body: RecurringInput, session=Depends(db, scope="function")):
        get_or_error(session, Category, body.category_id)
        if minor(body.expected) < 0:
            raise ValueError("Negative recurring amount")
        values = body.model_dump(exclude={"expected"})
        values["expected_minor"] = minor(body.expected)
        return audited_update(session, get_or_error(session, RecurringExpense, key), values)

    @app.delete("/api/recurring/{key}")
    def delete_recurring(key: str, session=Depends(db, scope="function")):
        # A planning reminder only: nothing references it, so it can always be removed (audited).
        item = get_or_error(session, RecurringExpense, key)
        session.add(
            AuditEvent(
                entity_type="recurring_expenses",
                entity_id=item.id,
                action="delete",
                before=serialize(item),
                after={},
            )
        )
        session.delete(item)
        return {"deleted": key}

    @app.get("/api/rules")
    def rules(session=Depends(db, scope="function")):
        return [
            serialize(x)
            for x in session.scalars(select(ClassificationRule).order_by(ClassificationRule.priority))
        ]

    @app.post("/api/rules/{key}/disable")
    def disable_rule(key: str, session=Depends(db, scope="function")):
        return audited_update(session, get_or_error(session, ClassificationRule, key), {"active": False})

    static = Path(
        os.getenv("FAMILY_BUDGET_STATIC", Path(__file__).resolve().parents[3] / "frontend" / "dist")
    )
    if static.is_dir():
        app.mount("/", StaticFiles(directory=static, html=True), name="frontend")
    return app
