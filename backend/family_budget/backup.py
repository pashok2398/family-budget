"""Authenticated encrypted backups, including immutable source documents and a consistent DB snapshot."""

from contextlib import closing
from .attachments import path_for
from .config import require_external
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import zipfile
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

MAGIC = b"FAMBUD01"
MAX_BACKUP = 1024 * 1024 * 1024


def key(password, salt):
    if len(password) < 12:
        raise ValueError("Use a backup passphrase of at least 12 characters")
    return Scrypt(salt=salt, length=32, n=2**15, r=8, p=1).derive(password.encode())


def archive(root: Path) -> bytes:
    """The verified, unencrypted backup zip. Callers encrypt it: backup() with a passphrase, or the scheduled
    backup by piping it into age, so plaintext never reaches a file."""
    if not (root / "budget.sqlite3").exists():
        raise ValueError("Database not initialized")
    with tempfile.TemporaryDirectory() as temp:
        snapshot = Path(temp) / "budget.sqlite3"
        # Freeze app writers while the separate read connection creates its WAL-aware snapshot.
        with (
            closing(sqlite3.connect(root / "budget.sqlite3")) as lock,
            closing(sqlite3.connect(root / "budget.sqlite3")) as source,
            closing(sqlite3.connect(snapshot)) as target,
        ):
            lock.execute("BEGIN IMMEDIATE")
            source.backup(target)
            lock.rollback()
        files = {"budget.sqlite3": snapshot.read_bytes()}
        with closing(sqlite3.connect(snapshot)) as conn:
            if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("Database integrity check failed")
            for sha, ext in conn.execute("SELECT sha256, extension FROM source_documents"):
                data = (root / "imports" / f"{sha}{ext}").read_bytes()
                if hashlib.sha256(data).hexdigest() != sha:
                    raise ValueError("Source document checksum mismatch")
                files[f"imports/{sha}{ext}"] = data
            has_receipts = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'attachments'"
            ).fetchone()  # databases from before migration 0004 have none
            receipts = (
                conn.execute("SELECT DISTINCT sha256, content_type FROM attachments") if has_receipts else []
            )
            for sha, content_type in receipts:
                name = path_for(Path("."), sha, content_type).as_posix().removeprefix("./")
                data = (root / name).read_bytes()
                if hashlib.sha256(data).hexdigest() != sha:
                    raise ValueError("Receipt checksum mismatch")
                files[name] = data
        if sum(len(x) for x in files.values()) > MAX_BACKUP:
            raise ValueError("Backup exceeds MVP 1 GiB limit")
        manifest = {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, data in files.items():
                archive.writestr(name, data)
            archive.writestr("manifest.json", json.dumps(manifest))
        return buffer.getvalue()


def backup(root: Path, destination: Path, password: str):
    destination = require_external(destination)
    if destination.exists():
        raise ValueError("Backup destination already exists")
    plain = archive(root)
    salt, nonce = os.urandom(16), os.urandom(12)
    ciphertext = AESGCM(key(password, salt)).encrypt(nonce, plain, MAGIC)
    fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as out:
        out.write(MAGIC + salt + nonce + ciphertext)


def restore(archive_path: Path, destination: Path, password: str):
    destination = require_external(destination)
    if destination.exists() and any(destination.iterdir()):
        raise ValueError("Restore requires an empty directory; stop the service first")
    if archive_path.stat().st_size > MAX_BACKUP:
        raise ValueError("Backup exceeds size limit")
    encrypted = archive_path.read_bytes()
    if encrypted[:8] != MAGIC:
        raise ValueError("Unsupported backup format")
    try:
        plain = AESGCM(key(password, encrypted[8:24])).decrypt(encrypted[24:36], encrypted[36:], MAGIC)
    except Exception:
        raise ValueError("Wrong passphrase or damaged backup") from None
    restore_archive(plain, destination)


def restore_archive(plain: bytes, destination: Path):
    """Validate an unencrypted backup zip completely, then install it into an empty directory."""
    destination = require_external(destination)
    if destination.exists() and any(destination.iterdir()):
        raise ValueError("Restore requires an empty directory; stop the service first")
    if len(plain) > MAX_BACKUP:
        raise ValueError("Backup exceeds size limit")
    try:
        opened = zipfile.ZipFile(io.BytesIO(plain))
    except zipfile.BadZipFile:
        raise ValueError("Invalid backup archive") from None
    with opened as archive:
        infos = archive.infolist()
        if sum(i.file_size for i in infos) > MAX_BACKUP or len({i.filename for i in infos}) != len(infos):
            raise ValueError("Invalid backup archive")
        manifest = json.loads(archive.read("manifest.json"))
        if set(manifest) != set(archive.namelist()) - {"manifest.json"} or "budget.sqlite3" not in manifest:
            raise ValueError("Invalid manifest")
        files = {}
        for name, sha in manifest.items():
            path = Path(name)
            if (
                path.is_absolute()
                or ".." in path.parts
                or (
                    name != "budget.sqlite3"
                    and (len(path.parts) != 2 or path.parts[0] not in {"imports", "attachments"})
                )
            ):
                raise ValueError("Unsafe backup path")
            data = archive.read(name)
            if hashlib.sha256(data).hexdigest() != sha:
                raise ValueError("Backup checksum mismatch")
            files[name] = data
    # Validate everything before touching the target. Install into a temporary sibling directory.
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent, prefix=".family-budget-restore-") as stage:
        staging = Path(stage) / "data"
        staging.mkdir(mode=0o700)
        for name, data in files.items():
            path = staging / name
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as out:
                out.write(data)
        with closing(sqlite3.connect(staging / "budget.sqlite3")) as conn:
            if (
                conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok"
                or conn.execute("PRAGMA foreign_key_check").fetchall()
            ):
                raise ValueError("Restored database integrity failure")
        if destination.exists():
            destination.rmdir()
        staging.rename(destination)
