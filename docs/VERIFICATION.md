# Verification record

Environment: Python 3.14.2, Node 22.22.0. Application supports Python 3.12+; Docker targets Python 3.12.

Run during implementation:

- `python3 -m compileall -q backend tests`: passed.
- `ruff check backend tests`: passed after formatting/cleanup.
- `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest -q`: 16 passed, 5 integration modules skipped (dependencies unavailable).
- Exact-decimal, Unicode normalization/date validation, scoped rule matching, encrypted backup/restore, passphrase failure, archive tampering and source checksum tests passed.
- Python wheel packaging with `pip wheel --no-deps --no-build-isolation .`: passed; migration Python files and Alembic template included.
- Cached TypeScript compiler transpilation of all frontend TypeScript/TSX: zero syntax errors. Five Node assertions against compiled exact-money helpers passed. This is **not** a full dependency-aware TypeScript build.
- Frozen migration DDL independently translated and executed with stdlib SQLite: 24 tables, integrity check, foreign-key rejection and immutable-audit trigger checks passed. This is **not** an Alembic/ORM integration run.

Not verified in this environment:

- SQLAlchemy/FastAPI/Pandas/Alembic/PDF integration suites: required dependencies were absent. `pip install -e '.[test]'` failed because package-registry DNS resolution is blocked.
- Full TypeScript build, Vitest and browser workflow: `npm install --offline` failed because TanStack Query was not cached; network package installation is unavailable.
- Docker build/start: Docker socket access was denied.
- Native Hapoalim layouts and Isracard PDFs: no validated samples. The Isracard PDF export has no table header rows and extracts Hebrew in visual (reversed) order, so it is not supported.

Verified with real samples: Isracard website XLSX export (`isracard_web_xlsx_v1`), two cards × two statement months, each scheduled section equal to its printed and header totals. Regression tests use a synthetic workbook copying the layout.

Run the full commands in README on a machine with package download access before using real financial data. This record distinguishes implemented code from verified runtime behavior.
