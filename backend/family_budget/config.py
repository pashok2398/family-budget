import os
from pathlib import Path


def require_external(path: Path) -> Path:
    path = path.expanduser().resolve()
    for parent in Path(__file__).resolve().parents:
        if (parent / "pyproject.toml").is_file() and (parent / "backend" / "family_budget").is_dir():
            if path == parent or parent in path.parents:
                raise ValueError("Financial data and backups must stay outside the source checkout")
            break
    return path


def data_dir() -> Path:
    path = Path(os.getenv("FAMILY_BUDGET_DATA_DIR", "~/.local/share/family-budget")).expanduser().resolve()
    path = require_external(path)
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


def allowed_hosts() -> list[str]:
    extra = os.getenv("FAMILY_BUDGET_ALLOWED_HOSTS", "")
    return ["localhost", "127.0.0.1", "[::1]", "testserver"] + [
        h.strip() for h in extra.split(",") if h.strip()
    ]


def inbox_dir() -> Path | None:
    """Read-only folder of fetcher output (fetcher/lib.mjs), offered for import preview."""
    value = os.getenv("FAMILY_BUDGET_INBOX")
    return Path(value) if value else None


def fetcher_url() -> str | None:
    """Internal address of the Isracard / bank fetcher service; it holds the logins, the app never does."""
    return os.getenv("FAMILY_BUDGET_FETCHER_URL") or None


INBOX_FILE = r"(isracard|hapoalim)-\d{4}-\d{12}\.json"
MAX_UPLOAD = 20 * 1024 * 1024
AI_ENABLED = False  # No provider, network client, telemetry or content-sharing integration.
