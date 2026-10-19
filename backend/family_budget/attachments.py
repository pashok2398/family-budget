"""Receipts and invoices attached to transactions. Files are identified by their content (never by the
uploaded name or declared type), stored once per hash outside the database and served only as downloads."""

import hashlib
import os
from pathlib import Path

MAX_ATTACHMENT = 10 * 1024 * 1024
TYPES = {
    "application/pdf": ".pdf",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/heic": ".heic",
}


def detect(content: bytes) -> str:
    if content.startswith(b"%PDF-"):
        return "application/pdf"
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "image/webp"
    if content[4:8] == b"ftyp" and content[8:12] in {b"heic", b"heix", b"mif1", b"msf1"}:
        return "image/heic"
    raise ValueError("Receipts must be PDF, JPEG, PNG, WebP or HEIC files")


def safe_name(name: str) -> str:
    base = Path(name or "receipt").name.replace("\x00", "").strip() or "receipt"
    return base[-200:]


def path_for(root: Path, sha: str, content_type: str) -> Path:
    return root / "attachments" / f"{sha}{TYPES[content_type]}"


def store(root: Path, content: bytes) -> tuple[str, str]:
    if not content:
        raise ValueError("Empty file")
    if len(content) > MAX_ATTACHMENT:
        raise ValueError("Receipts are limited to 10 MiB")
    content_type = detect(content)
    sha = hashlib.sha256(content).hexdigest()
    path = path_for(root, sha, content_type)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        if hashlib.sha256(path.read_bytes()).hexdigest() != sha:
            raise ValueError("Stored receipt integrity check failed") from None
    else:
        with os.fdopen(fd, "wb") as out:
            out.write(content)
    return sha, content_type
