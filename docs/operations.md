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

What is backed up: only what cannot be recreated, the DB, `imports/` (original statements) and receipts.
Not backed up on purpose: bank logins, fetcher state, inbox, `.env` (re-login / `.env.example` restore them).

- One-time: `sudo make backup-setup` asks for a passphrase, seals it with systemd-creds (TPM2 + host key) in
  `/etc/credstore.encrypted/family-budget-backup.cred`, installs `family-budget-backup.{service,timer}`.
  Keep the passphrase in a password manager too: the sealed copy only decrypts on this machine.
- Nightly 03:30 (`deploy/backup-run.sh`): `family-budget backup --passphrase-stdin` into
  `/srv/family-budget/backups/budget-<timestamp>.fbb`, then a test restore into the container's tmpfs, then
  archives older than `KEEP_DAYS` (30) are deleted.
- `make backup-now` runs one immediately, `make backup-status` shows the timer, last result and archives.
- Off-site: with `/etc/family-budget-backup.env` (from `deploy/family-budget-backup.env.example`, mode 600) each
  verified archive is encrypted again with `age` to the owner's SSH/age public key, uploaded with rclone to
  `BACKUP_REMOTE` (here `gdrive:Backups/family-budget`, rclone config in `/root/.config/rclone`), and pruned there
  after `KEEP_DAYS` too. Needs `age` and `rclone` on the host.
- Restore from Drive: `rclone copy gdrive:Backups/family-budget/<file>.fbb.age .`, then
  `age -d -i ~/.ssh/id_ed25519 <file>.fbb.age > <file>.fbb` on the machine holding the private key, then the
  normal restore with the backup passphrase.
- Restore: README "Encrypted backup and restore".
