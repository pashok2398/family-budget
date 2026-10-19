# Operations

## Containers (compose.yaml)

- `budget` (fin-budget-1): the app. Data bind `/srv/family-budget` → `/data`, inbox `/srv/family-budget-inbox` → `/inbox`.
- `gateway`: nginx, the only service published on the LAN (`FAMILY_BUDGET_BIND` in `.env`). HTTP basic auth on
  everything: hash in `/etc/family-budget/htpasswd` (root:101, 640; override `FAMILY_BUDGET_HTPASSWD`), set or
  changed with `sudo make web-password` (also recreates the gateway). `deploy-quick` refuses to run without it.
  API calls from scripts need `-u user:password` plus the `X-Family-Budget: local` header.
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

Encryption is public-key only (`age`): the host holds the owner's public key, never a secret, and only the
owner's private key (e.g. `~/.ssh/id_ed25519`) can open an archive.

- Config: `/etc/family-budget-backup.env` (from `deploy/family-budget-backup.env.example`, mode 600):
  `BACKUP_AGE_RECIPIENT` (ssh-ed25519 or age1 public key), optional `BACKUP_REMOTE` (here
  `gdrive:Backups/family-budget`) and `RCLONE_CONFIG`. Needs `age` and `rclone` on the host.
- Install: `make install-backup` (as root) installs and enables `family-budget-backup.{service,timer}`.
- Nightly 03:30 (`deploy/backup-run.sh`): `family-budget backup - --plain` streams the unencrypted zip out of the
  container through a pipe into `age` (plaintext never touches a file) and, via a FIFO, into a test restore in the
  container's tmpfs. The archive `<data dir>/backups/budget-<timestamp>.zip.age` is kept only if that restore
  passes. Then upload with rclone (MD5-verified) and prune local and remote copies after `KEEP_DAYS` (30).
- `make backup-now` runs one immediately, `make backup-status` shows the timer, last result and archives.
- Restore (on the machine with the private key):

  ```bash
  rclone copy gdrive:Backups/family-budget/<file>.zip.age .
  age -d -i ~/.ssh/id_ed25519 <file>.zip.age | family-budget restore - /empty/dir --plain
  ```

  In Docker: pipe into `docker compose exec -T budget family-budget restore - /tmp/restored --plain`, or restore
  with a local install. Then point `FAMILY_BUDGET_HOST_DATA` at the restored directory (README "Encrypted backup
  and restore" for switching the live service).
- The manual `family-budget backup <file>.fbb` (passphrase, AES-GCM) still exists for one-off archives.
