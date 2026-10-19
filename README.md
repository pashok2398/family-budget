# בבית · Family Budget

A local-first Israeli household finance MVP: FastAPI, SQLAlchemy 2, SQLite/WAL, Alembic, React/TypeScript, Vite, TanStack Query and Tailwind. Hebrew RTL dashboard, weekly review, import previews, conservative deduplication, reversible reconciliation, scoped classification, editable budgets and encrypted backups. No accounts with external services, analytics, telemetry, or AI calls.

**Already running the app?** Start with the [user guide](GUIDE.md): first-time setup, importing statements, weekly review, dashboard meanings, budgets, funds, capabilities and troubleshooting.

**Status:** implemented MVP source, synthetic fixtures and automated tests. One native layout is validated: the Isracard website **XLSX** transaction export (`isracard_web_xlsx_v1`, see below). Native Hapoalim layouts and Isracard PDFs are **not validated**. The adapters otherwise support an explicit user mapping and the canonical interchange format below. Unknown native PDF layouts fail clearly. Do not assume compatibility from a bank's name. See [verification status](docs/VERIFICATION.md) for checks actually run in the implementation environment.

Setting up a new host: follow [docs/setup.md](docs/setup.md).

## Quick start: local Python + Node

Python 3.12+ and Node 22.12+ are required. Package installation needs internet access once; running the application does not.

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[test]'
cd frontend
npm install
npm run build
cd ..
.venv/bin/family-budget init
.venv/bin/family-budget serve
```

Open **http://127.0.0.1:8000**. The CLI binds loopback by default. SQLite and original imports are stored in `~/.local/share/family-budget`, outside this repository. To choose another location:

```bash
export FAMILY_BUDGET_DATA_DIR=/absolute/path/outside/repository
.venv/bin/family-budget init
.venv/bin/family-budget serve
```

The compiled frontend is served by FastAPI on the same origin. For frontend development, run `npm run dev` in `frontend` alongside the backend. Vite proxies `/api`; use the exact hostname shown by Vite, and do not rewrite the proxy Host header. Native PDF extraction uses Linux resource limits; use Docker on Windows/macOS.

## Docker Compose

The container runs as non-root UID 10001, with a read-only root filesystem, a bounded temporary filesystem, dropped Linux capabilities and localhost-only host publishing.

```bash
sudo install -d -m 700 -o 10001 -g 10001 /srv/family-budget
export FAMILY_BUDGET_HOST_DATA=/srv/family-budget
docker compose up --build -d
```

Open http://127.0.0.1:8000. The bind directory holds the database and original statements; removing the container does not remove the data. Never use a directory inside the Git checkout. Do not expose the port publicly: this MVP is a **single trusted local household workspace**, without multi-user authentication. For remote access, use an authenticated private tunnel/VPN and an appropriately configured trusted origin; remote deployment is outside the current default configuration.

## First review

1. In **הגדרות**, add an account alias and any credit cards. Full account numbers are unnecessary.
2. Choose **ייבוא דוח**, select the source, and upload a CSV/XLSX or explicitly mapped PDF table.
3. Review the preview, totals, rejected rows and ambiguous overlaps. An exact document re-upload reopens the same batch. For an uncommitted preview, changing the mapping or expected total reparses it and records the change. No financial transactions exist until commit.
4. Explicitly label uncertain rows as a distinct new purchase, a duplicate of a specific existing transaction, or rejected. Rejected rows are retained. A statement-total mismatch blocks commit.
5. Confirm the import. Known rules run; uncertain classifications go to **סקירה שבועית**.
6. Confirm suggestions in batches or edit individual transactions, their purpose, category, household/pub/project/investment scope, allocation date, and signed splits.
7. Approve settlement/transfer/refund proposals. Unmatched transactions remain visible; matching is never inferred from equal amounts alone. A settled card purchase may also be linked to a refund; the two relationships can be undone independently.
8. Review the dashboard and budget. Budget limits change only through an explicit save.

### Community projects and receipts

A project marked **פרויקט קהילתי** (in הגדרות) pays the household back. Choosing it under **בשביל מי?** in a transaction makes that purchase a receivable: it leaves household consumption, appears as **לגבייה** in the month's charges and in **חייבים לי** with its age. **קבלת החזר** records money that came back (bank, Bit, PayBox, cash or other) and allocates it to one or more of that project's expenses, oldest first for a partial amount; recording can be undone, and an expense that was already (partly) reimbursed cannot be moved back to the household until its payments are undone. Receipts (PDF, JPEG, PNG, WebP, HEIC up to 10 MiB, recognised by content) attach to any transaction, are stored by hash under the data directory, served only as downloads and included in encrypted backups.

Manual corrections are protected by a version check, recorded in append-only audit history, and improve future *suggestions*. Creating a merchant-wide rule is a separate unchecked-by-default option. A date/amount exception is not silently promoted to a global rule. Conflicting rules require review.

## Import contract and parser limits

The following are **application interchange columns**, not claims about banks' exported field names:

```csv
transaction_date,posting_date,merchant,amount,currency,reference,kind
2026-10-05,2026-10-10,Example Store,-100.25,ILS,synthetic-reference,purchase
```

Required columns: `transaction_date`, `posting_date`, `merchant`, `amount`, `currency`. Dates require a four-digit year (`YYYY-MM-DD`, `DD/MM/YYYY`, or `DD.MM.YYYY`). Expenses are negative and credits positive. CSV encoding is UTF-8 (BOM accepted). XLSX uses the first sheet. Preserve decimal amounts as text when exporting; do not round them.

Optional columns: `description`, `reference`, `allocation_date`, `kind`, `original_amount`, `original_currency`, and the installment fields described below. No exchange rates are inferred. The normalized amount/currency must be explicitly provided when different from the original charge.

An explicit mapping maps canonical names to exact source headers. The Hebrew example is synthetic:

```json
{
  "columns": {
    "transaction_date": "תאריך עסקה",
    "posting_date": "תאריך חיוב",
    "merchant": "שם עסק",
    "amount": "סכום",
    "currency": "מטבע",
    "reference": "אסמכתא"
  },
  "sign": "debit_positive",
  "expected_total": "-364.87"
}
```

`expected_total` is the signed normalized sum of all transaction rows, **not an account balance**. It must be a decimal string. It is validated exactly when supplied. Totals not supplied are clearly marked unverified. Mixed currency statements cannot validate one combined total.

PDF requires `"layout":"user_mapped_table_v1"` plus a column mapping. PyMuPDF checks text readability; pdfplumber extracts ruled tables in a separate time/memory-bounded process. Hebrew logical-order text is preserved; bidi control characters are removed only for matching. The adapter does not reverse Hebrew strings or guess table order. Scans, encrypted PDFs, pages without reliable tables and unknown layouts are rejected. There is no OCR. Download the original source from the preview when needed.

### Isracard website XLSX export (`isracard_web_xlsx_v1`)

Validated against real exports of two cards across two statement months. Upload the "פירוט עסקאות" Excel file from the Isracard website with the **ישראכרט** source and the matching card; no mapping is needed. The layout is detected from its title and section headings, and any change in headings or columns fails the import instead of guessing.

- Imported: **עסקאות למועד חיוב** (posted on the statement charge date) and **עסקאות בחיוב מחוץ למועד** (posted on the **חיוב בחשבון הבנק** date). Skipped: **עסקאות שטרם נקלטו** (not final) and the **עסקאות בחיוב עתידי** summary.
- Amounts are the charged amount and currency (**סכום חיוב / מטבע חיוב**). The purchase amount/currency is kept as the original; the symbols ₪, $, € and L (Romanian leu, RON) are mapped. Unknown symbols keep the charged amount and record the original in the description.
- The scheduled section must equal its printed total **and** the header charge total, or parsing fails. The off-cycle section has no printed total and is reported separately as unverified.
- If the card has its last four digits set, an export for a different card is refused.
- Statements Isracard marks **החיוב אינו סופי** are refused unless the options contain `{"allow_open": true}`. Re-export after the charge date for final figures.
- Installments (`תשלום N מתוך M`) are imported as ordinary charges with the note in the description; the export has no remaining commitment, which is never estimated.

### Isracard and Bank Hapoalim fetcher (`isracard_scraper_json_v1`, `hapoalim_scraper_json_v1`)

An optional, on-demand container (`fetcher/`) logs in to Isracard and Bank Hapoalim with [israeli-bank-scrapers](https://github.com/eshaham/israeli-bank-scrapers) 6.12.2 and writes one JSON file per card or bank account to `/srv/family-budget-inbox`. It never writes to the ledger: the app lists those files read-only in **ייבוא דוח**, and each one opens the normal preview (duplicates, review, explicit commit).

**Each login is sealed with `systemd-creds` (TPM2 + host key)** in `/etc/credstore.encrypted/family-budget-<source>.cred` (`isracard`, `hapoalim`); it decrypts only on this machine. The `family-budget-fetcher` systemd unit decrypts the sealed logins into its private credentials directory, and `deploy/fetcher-start.sh` pipes them as one JSON object to the fetcher container's stdin, so it is never a file the container or the app can read, and lives only in the fetcher's memory. The fetcher runs all the time, fetches daily at `FETCH_AT` (06:00 Asia/Jerusalem by default) and on request, and removes fetched files older than 45 days. Its port is not published; the app shows its status and a **משוך עכשיו** button in **ייבוא דוח** and never holds the login.

```bash
sudo make isracard-login     # asks for ID, first 6 card digits and password; seals them; installs and starts the fetcher
sudo make hapoalim-login     # asks for the bank user code and password; same
make fetch-isracard          # fetch now (same as the button)
make fetcher-status          # unit state, last and next run
make fetcher-logs            # recent fetcher output
```

`card6Digits` is the **first** six digits of the card used to log in; one login returns every card under it. The fetcher drops `rawTransaction` and prints only counts and date ranges.

- Mapping (read from the library source): `date` → purchase date, `processedDate` → posting (billing or actual off-cycle charge) date, both converted from UTC to Asia/Jerusalem; `chargedAmount/chargedCurrency` → amount; `originalAmount/originalCurrency` → original when the currency differs; `identifier` → voucher reference, zero-padded to the nine digits the website export prints. Pending transactions are skipped.
- The preview shows charges per billing date so they can be compared with the statement; there is no printed total to validate against.
- Fetched files and website exports recognise each other: a matching voucher with the same dates, amount, currency and merchant is "already imported", even when notes differ. A changed amount or date stays ambiguous.
- Bank Hapoalim (`hapoalim_scraper_json_v1`, checked against a real fetch of 49 transactions on 19.10.2026: references repeat — 16 distinct in 49): `date` → event date, `processedDate` → value date, signed ILS amounts, `description` → the bank's activity text, `memo` (transfer counterparty and message) → description. The bank reference (אסמכתא) repeats inside an account, so it is kept in the description and duplicates are found from dates, amount and texts. Rows with serial number 0 are pending and skipped. The preview shows the balance the bank reported. Purposes come from classification rules marked `"source": "bank"` (migration 0005, from the first real fetch): "ישראכרט בע\"מ" (monthly card bill) and "מסטרקרד" (an immediate-debit card charged per purchase) are card settlements, never spending, because the Isracard import already holds the purchases; mortgage (טפחות), child allowance, ATM cash and securities sales are confirmed. Transfers whose meaning depends on the household (own accounts, reimbursements, PayBox, projects) wait in review and do not count.
- **Bank Hapoalim SMS code:** the bank asks for a code on a device it does not know; israeli-bank-scrapers has no step for it, so `fetcher/hapoalim.mjs` replaces the step after login. In a fetch started from the app (**משוך עכשיו**) the fetcher waits up to 5 minutes for the code typed in the field that appears there (`POST /api/fetcher/code`, passed straight to the waiting login, never stored or logged), ticks the bank's remember-device option when offered, and logs only the page structure. Hapoalim keeps a browser profile (its cookies) in `/srv/family-budget-fetcher-state` (fetcher user only, mode 700) so the bank can recognise the device next time. Scheduled runs skip Hapoalim until a login has succeeded; if a scheduled run is asked for a code, it stops at once and Hapoalim goes back to manual-only, so at most one unrequested SMS.
- **Network:** the app and the fetcher are on an internal Docker network with no route to the internet (`compose.yaml`). `gateway` (nginx) is the only container published on the LAN and forwards to the app. `egress` (tinyproxy, `deploy/egress/`) is the fetcher's only way out: it accepts only the fetcher's fixed address and only HTTPS to `*.isracard.co.il` and `*.bankhapoalim.co.il` (`deploy/egress/allowed-domains`), so it also holds if a package misbehaves; the app cannot use it at all. The bank websites embed third-party trackers (Google Tag Manager/DoubleClick, Facebook, Instagram, Outbrain, AppNexus, UserWay, Cloudflare Insights, an Isracard logging host on Azure) and Chromium has its own Google services; all are refused, and `docker logs fin-egress-1` lists every allowed and refused host name. Verified 19.10.2026 with a full Isracard fetch (only `web.` and `digital.isracard.co.il` allowed) and direct connection attempts from both containers (unreachable). Adding a source means adding its domain there.
- The fetcher runs Chromium without its own sandbox (the container drops all capabilities, is read-only and has resource limits). It breaks when Isracard changes its site; the website XLSX upload remains available.

Before adding any other native layout: supply redacted real examples, verify headings and sign conventions, compare extraction against printed totals, then add a versioned adapter and regression fixtures. [Synthetic fixtures](tests/fixtures/README.md) are intentionally labeled and never loaded as real household history.

### Identity and overlaps

- Documents are SHA-256 addressed and immutable; the account/card source is separately bound to a batch.
- A supplied source reference must be stable and unique for that source; conflicting details are ambiguous.
- Without a reference, a semantic fingerprint plus occurrence count preserves legitimate same-day identical transactions in one statement.
- Overlap between different documents without source references requires a human choice. It is never silently deduplicated.
- Commit runs in one serialized write transaction, rechecks historical rows, and is repeatable. A stale preview must be refreshed.
- The same document cannot be committed a second time under the same source, even if renamed.

### Installments

To import installments provide **all five** fields: `purchase_id`, `installment_number`, `installment_count`, `original_purchase_amount`, `remaining_commitment`. Original total and remaining commitment are positive decimal strings; the charged amount is the actual signed monthly charge. With no explicit allocation date, installments allocate by posting date. Regular purchases allocate by transaction date. No future charges are generated. The latest known installment per source/purchase determines remaining commitment. Irregular installment amounts are supported because the balance is explicit, not estimated by multiplication.

## Accounting rules

- **Cash flow:** all imported bank-account movements by posting date, including card settlements. Card detail rows never enter bank cash flow. This is a movement total, not a verified account balance.
- **Consumption:** confirmed household purchases, interest charges and ordinary refunds by allocation date. Reimbursable outlays and reimbursement credits are tracked separately. Card settlements, own-account transfers, cash withdrawals, loan proceeds and investment sales are not purchases.
- **Debt service:** full mortgage/loan payments appear separately. Principal/interest can be attached in Settings or through the loan-payment API; missing breakdowns are not invented.
- **Savings contributions:** explicitly classified savings/investment transfers; never inferred from a favorable budget variance.
- **Pub/projects/investments:** separate from ordinary household consumption; project budgets are explicit. Aggregate seed evidence remains a suggestion until exact records are identified.
- **Sinking funds:** allocations are targets, not expenses or bank transfers. Signed fund movements maintain earmarked balances across months. Linking an actual purchase does not duplicate its consumption. A balance is an earmarking ledger, not a claim that cash exists in a separate account.
- **Remaining budget:** ordinary plan (excluding fund/savings allocations) minus confirmed ordinary spending and debt service. Fund spending and savings are shown separately. Pending classifications and foreign-currency rows are visibly excluded; this number is never labeled actual savings.
- **Refunds:** purchases explicitly marked as reimbursable and their matched reimbursement credits are tracked separately from household consumption, with outstanding amounts shown as expected receivables. Ordinary purchase refunds still offset consumption. Exact full refunds/reimbursements can link to originating expenses; the credit inherits the expense scope/category. Partial and split refunds require manual allocation in this MVP and are not automatically matched.
- **Suggested adjustments:** require reviewed ILS records in each of the three preceding months. Statement completeness cannot be inferred, so the user must verify coverage before accepting a suggestion. No automatic budget edits.

The example ₪32,000 template (edit `backend/family_budget/seed.py` for your own household) is seeded, including the ₪1,000 maintenance and ₪1,200 vacation funds. Salary sources are separate; neither salary split nor outstanding loan principal is invented. Allocations are editable in each month's saved budget, and all categories are editable. Monthly edits do not automatically change other months. Seed classification provenance is retained.

## REST API

The local OpenAPI document at `/api/openapi.json` lists exact Pydantic schemas. Cloud-hosted Swagger/Redoc assets are disabled. All mutation calls must send `X-Family-Budget: local`. Browser origins and Host headers are checked; no cross-origin API is enabled. API money is a decimal string, never a JSON float. The dashboard reports ILS only and surfaces excluded currencies.

Main routes:

| Route | Purpose |
|---|---|
| `GET /api/settings`, `POST /api/accounts`, `POST /api/cards` | Sources and catalog |
| `POST /api/imports/preview` | Multipart file + source + optional JSON mapping |
| `GET /api/imports/{id}`, `PATCH /api/import-rows/{id}` | Preview and explicit row decisions |
| `POST /api/imports/{id}/refresh`, `POST /api/imports/{id}/commit` | Recheck and atomic commit |
| `GET /api/transactions` | Month/source/card/category/project/scope/review filters |
| `POST /api/transactions/review`, `GET /api/transactions/{id}/history` | Batch corrections and audit |
| `GET/POST /api/reconciliation`, `POST /api/reconciliation/{id}/undo` | Explainable matching |
| `GET /api/dashboard?month=2026-10` | Metrics, trends, funds, commitments, suggestions |
| `PUT /api/budgets/{month}` | Explicit plan edits |
| `POST /api/categories`, `PUT /api/categories/{id}` | Editable categories |
| `POST /api/projects`, `PUT /api/projects/{id}` | Project budgets |
| `POST /api/funds`, `GET/POST /api/funds/{id}/movements` | Carry-forward fund ledger |
| `GET /api/rules`, `POST /api/rules/{id}/disable` | Inspect/disable classifications |
| `POST /api/income-sources`, `PUT /api/income-sources/{id}` | Variable income sources |
| `POST /api/loans`, `PUT /api/loans/{id}`, `GET/POST /api/loans/{id}/payments` | Verified principal and interest (payment corrections via PUT) |
| `POST /api/recurring`, `PUT /api/recurring/{id}` | Recurring expense reminders |

## CLI

Commands print private data only when you explicitly request them; redirect output only to a protected location. Server logs disable HTTP access logging and never log uploaded statement contents.

```bash
family-budget init
family-budget account "חשבון משותף" --institution hapoalim
family-budget card ACCOUNT_ID "ישראכרט משפחתי"
family-budget import /private/bank.csv --account ACCOUNT_ID --adapter hapoalim
family-budget import /private/card.xlsx --account ACCOUNT_ID --card CARD_ID --adapter isracard --options /private/mapping.json
family-budget resolve ROW_ID new
family-budget resolve ROW_ID duplicate:TRANSACTION_ID
family-budget resolve ROW_ID reject
family-budget commit BATCH_ID
family-budget review
family-budget review --decisions /private/corrections.json
```

Use `family-budget --data-dir /absolute/path ...` to select another store. Review decisions use the same `{ "items": [...] }` payload as the API and include the current transaction `version`.

## Encrypted backup and restore

Backups use an online SQLite backup (WAL-safe), include all registered original documents, verify their SHA-256 hashes, and encrypt the archive with AES-256-GCM and a random salt/nonce. Scrypt derives a key from an interactively entered passphrase. Passphrases are never command arguments, stored settings, or log messages. The MVP backup limit is 1 GiB. Backups themselves must live outside the Git checkout.

```bash
family-budget backup /private/backups/household-2026-10.fbb
# Enter and repeat a strong passphrase of at least 12 characters.
family-budget restore /private/backups/household-2026-10.fbb /private/restored-budget
family-budget --data-dir /private/restored-budget serve
```

Restore only into an empty directory. It verifies authentication, manifest hashes, SQLite integrity and foreign keys before installing the restored directory. It does not overwrite a live database. Keep your passphrase separately; it is not recoverable. Test restore periodically.

For Docker:

```bash
docker compose exec budget family-budget backup /data/monthly.fbb
# Securely copy that encrypted file to independent storage.
docker compose stop budget
# Restore to a fresh host directory using the local CLI, verify it, then update the bind directory.
```

The live database is not encrypted at rest; use full-disk encryption and restrictive host directory permissions. PDF isolation limits resources; it is not a complete hostile-document sandbox. Keep dependencies patched, and run the hardened container when importing untrusted documents. No transaction content is ever sent to an LLM; `AI_ENABLED` is fixed false and there is no provider client.

## Tests and migrations

```bash
.venv/bin/pytest
cd frontend
npm test
npm run build
cd ..
.venv/bin/alembic upgrade head
```

The application also upgrades the schema on startup. Migrations are packaged with the Python application, not dynamically generated from live models. Tests cover exact decimals, classification exceptions, overlapping imports, reference conflicts, rejected records, stale previews, accounting separation, reversible matches, splits, fund balances, API origin checks and encrypted restore. Integration tests skip when their dependencies are unavailable; **skipped is not passed**. [Architecture](docs/ARCHITECTURE.md) documents the ledger design.

Future work requiring real evidence: native bank-layout adapters; verified OCR; partial/many-to-one refund allocation; automatic FX conversion with explicit rates; multi-user authorization. These are not silently approximated by this MVP.
