# Setup on a new host

The app runs in Docker only. Python and Node on the host are needed just for `make test`.

## Prerequisites

- Linux host with Docker and the compose plugin.
- For the bank fetcher: systemd with `systemd-creds` and a TPM2 chip (logins are sealed to the machine).
- For tests: Python 3.12+, Node 22.12+.

## 1. Code and dev toolchain

```bash
git clone <repo> fin && cd fin
make install          # venv + npm packages + docs-check git hook
make test
```

## 2. Data directories (outside the checkout)

UIDs are fixed by compose: app 10001, fetcher 10002.

```bash
sudo install -d -m 700  -o 10001 -g 10001 /srv/family-budget                 # DB, statements, receipts
sudo install -d -m 2750 -o 10002 -g 10001 /srv/family-budget-inbox           # fetcher writes, app reads
sudo install -d -m 700  -o 10002 -g 10002 /srv/family-budget-fetcher-state   # Hapoalim browser profile
```

## 3. Configuration

```bash
cp .env.example .env
```

Set `FAMILY_BUDGET_HOST_DATA=/srv/family-budget`. For LAN access set `FAMILY_BUDGET_BIND` and
`FAMILY_BUDGET_ALLOWED_HOSTS` to the host's LAN IP; leave unset for loopback only. There is no login: anyone who can
reach the port has full access.

## 4. Your household baseline (before the first start)

On first start against an empty DB, the app runs migrations and loads `backend/family_budget/seed.py`: categories,
a budget template, funds, income sources, loans, a sample project and classification rules. It is an example
household. Edit it to your own amounts, employers and merchants before step 5, or edit everything later in the UI.
Seed never runs again once categories exist. Migration 0005 adds generic Bank Hapoalim rules (card bills, mortgage,
child allowance, ATM, securities sales).

## 5. Start

```bash
make deploy           # tests, build, start, wait for healthy
```

Open `http://<bind address>:8000`.

## 6. In-app first run (הגדרות)

1. **חודש פיננסי**: period start day (e.g. 10 to match card bills), card spending basis (charge date or purchase
   date), and whether every imported row waits for review.
2. **חשבונות וכרטיסים**: add the bank account, then each card with its last four digits (lets the importer reject a
   statement of the wrong card).
3. Check categories, income sources, loans and recurring expenses; adjust the budget template in תקציב.

## 7. Bank fetcher (optional)

```bash
sudo make isracard-login     # ID, first 6 card digits, password → sealed, installs and starts the fetcher
sudo make hapoalim-login     # user code, password
make fetcher-status
```

Hapoalim asks for an SMS code on the first login: start it with **משוך עכשיו** in ייבוא דוח and type the code there.
Fetched files appear in ייבוא דוח; nothing reaches the ledger until you commit a preview. Details: README, fetcher
section.

## 8. Backups

Backups are encrypted to your SSH (or age) public key; only your private key opens them.

```bash
sudo dnf install age rclone && rclone config          # rclone only for an off-site copy (e.g. Google Drive)
sudo cp deploy/family-budget-backup.env.example /etc/family-budget-backup.env
sudo chmod 600 /etc/family-budget-backup.env           # set BACKUP_AGE_RECIPIENT, optionally BACKUP_REMOTE
sudo make install-backup                               # nightly 03:30 timer
sudo make backup-now
```

See [operations.md](operations.md#backups).

## 9. First import

Prefer the Isracard website XLSX for history (it has exact off-cycle charge dates, see
[ingestion.md](ingestion.md)), then let the fetcher keep it current. Walkthrough for the user: `GUIDE.md`.
