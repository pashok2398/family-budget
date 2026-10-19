# Conventions

## Code

- Python: ruff, line length 110. Run `ruff format` on the files you changed only; formatting the whole tree adds
  unrelated churn. Short comments that explain why, matching the existing density.
- Frontend: all UI lives in `frontend/src/App.tsx` as compact one-line JSX components; keep that style. UI text is
  Hebrew, code and identifiers English. Shared types in `types.ts`, API helpers in `api.ts`.
- New preferences: getter/setter in `periods.py` stored in `app_settings`, exposed by `current_preferences()` and
  `PreferencesInput` in the API, and in `Settings.preferences` on the frontend.

## Tests

- Backend tests in `tests/`, fixtures `ledger` and `fixture_dir` in `tests/conftest.py`. Every bug fix gets a
  regression test; check it fails without the fix.
- Parser tests build synthetic XLSX/JSON inline (`isracard_web_export`, `isracard_fetcher_export` in `test_parsers.py`).

## Privacy

The repo is meant to be shareable. Seed data (`seed.py`) is an anonymized example household. Tests, fixtures and docs
use invented merchants (Example …, Acme, Globex), example IPs and fake card digits. Before committing, check no real
names, employers, local businesses, card last-four digits, references or LAN addresses slipped in.

## Git

Branch `main`. Commit messages: imperative subject, body explains why.
