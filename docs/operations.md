# Operations

## Containers (compose.yaml)

- `budget` (fin-budget-1): the app. Data bind `/srv/family-budget` → `/data`, inbox `/srv/family-budget-inbox` → `/inbox`.
- `gateway`: nginx, the only service published on the LAN (`FAMILY_BUDGET_BIND` in `.env`).
- `egress`: proxy allowing HTTPS to bank domains only (`deploy/egress/allowed-domains`).
- `fetcher` (family-budget-fetcher): started by systemd `deploy/family-budget-fetcher.service` with TPM-sealed
  credentials. `make fetch-isracard`, `make fetcher-status`, `make fetcher-logs`.

The app runs only in Docker; the Makefile has no local run/build. `make install` sets up the dev toolchain (venv,
npm packages, docs-check git hook) used by `make test` (pytest, vitest, tsc). `make deploy` = test + `deploy-quick`
(rebuilds the image, waits for healthy). `make logs` shows the app container's recent output.

## Live data

- DB: `/srv/family-budget/budget.sqlite3` (WAL). Statement files: `/srv/family-budget/imports/<sha256>.<ext>`;
  `source_documents.sha256` links a document to its file.
- Inspect read-only: `sqlite3 -readonly /srv/family-budget/budget.sqlite3 "..."`.
- Useful tables: `import_batches` (summary JSON), `import_rows` (status, resolution, data JSON), `transactions`,
  `audit_events` (before/after JSON), `classification_rules`, `app_settings`.

## Data fixes

Never hand-edit SQL. Write a short script using the app models, back up first, and run it in the container:

```bash
cp /srv/family-budget/budget.sqlite3 /srv/family-budget/budget.sqlite3.pre-<fix>
docker exec -i fin-budget-1 python - < fix.py
```

Use `session_scope(make_engine(), True)` and add an `AuditEvent` for every change. Auto-mode may block writes into the
container; then hand the command to the user. Note `cp -i` exits 1 on "n" and stops an `&&` chain.

## Backups

Encrypted backup/restore via CLI, see README "Encrypted backup and restore".
