# Family Budget

Self-hosted household budget for an Israeli family: imports Isracard and Bank Hapoalim statements (uploaded files or the
headless fetcher), deduplicates, classifies, reconciles card settlements and transfers, and reports budgets in ILS.
Backend: Python 3.12+, FastAPI, SQLAlchemy, SQLite (WAL), Alembic. Frontend: React 19 + TanStack Query, Hebrew RTL UI.

## Commands

```bash
.venv/bin/pytest -q tests                  # backend tests (must all pass, skipped is not passed)
cd frontend && npm test && npx tsc --noEmit -p .   # frontend tests + typecheck
.venv/bin/ruff format <changed files>      # format only files you touched, line length 110
make deploy-quick                          # rebuild + restart the budget container, waits for healthy
```

## Map

| Path | What |
|---|---|
| `backend/family_budget/parsers/` | One versioned adapter per source layout (`isracard_web`, `isracard_scraper`, `hapoalim*`) |
| `backend/family_budget/ingestion/service.py` | Preview, duplicate assessment, resolve, commit |
| `backend/family_budget/classification/` | Rule engine, manual corrections, matching |
| `backend/family_budget/reporting/service.py` | Dashboard, charges by bill, consumption vs cash flow |
| `backend/family_budget/periods.py` | Financial month bounds and app preferences |
| `backend/family_budget/seed.py` | Example household loaded into an empty DB only |
| `frontend/src/App.tsx` | Entire UI, dense one-line JSX components; types in `types.ts` |
| `fetcher/` | Node scraper service, run by systemd, never by `docker compose up` |

## Read before working on

Load only the doc that matches the task:

- Ledger model, money, invariants: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- Imports, duplicates, charge dates, card bills: [docs/ingestion.md](docs/ingestion.md)
- Categories, rules, review queue: [docs/classification.md](docs/classification.md)
- Deploy, live data, DB inspection, data fixes: [docs/operations.md](docs/operations.md)
- Code style, tests, privacy rules: [docs/conventions.md](docs/conventions.md)
- What was verified against real samples: [docs/VERIFICATION.md](docs/VERIFICATION.md)

User-facing docs: `README.md` (setup, import contract, API, CLI) and `GUIDE.md` (end-user walkthrough).

## Hard rules

- Never commit real financial data, names, employers, card numbers or LAN addresses. Tests and seed use synthetic data.
- Money is signed integer minor units; never mix currencies; never guess FX.
- Schema changes go through a new Alembic migration in `backend/family_budget/migrations/versions/`.
- Live data at `/srv/family-budget` is the household's real ledger: read it with `sqlite3 -readonly`; change it only
  through app code with an audit event, after a backup, and with the user's go-ahead.
