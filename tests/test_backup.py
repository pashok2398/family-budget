import sqlite3
import hashlib
import pytest
from family_budget.backup import backup, restore


def fixture_database(root):
    root.mkdir()
    content = b"Synthetic,not real banking data\n"
    sha = hashlib.sha256(content).hexdigest()
    (root / "imports").mkdir()
    (root / "imports" / f"{sha}.csv").write_bytes(content)
    with sqlite3.connect(root / "budget.sqlite3") as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("CREATE TABLE source_documents (sha256 TEXT, extension TEXT)")
        db.execute("INSERT INTO source_documents VALUES (?,?)", (sha, ".csv"))
    return sha


def test_encrypted_backup_roundtrip_and_wrong_password(tmp_path):
    source = tmp_path / "source"
    sha = fixture_database(source)
    target = tmp_path / "backup.fbb"
    password = "a long test passphrase 123"
    backup(source, target, password)
    assert b"Synthetic" not in target.read_bytes()
    restored = tmp_path / "restored"
    restore(target, restored, password)
    assert (restored / "imports" / f"{sha}.csv").read_bytes() == (
        source / "imports" / f"{sha}.csv"
    ).read_bytes()
    with pytest.raises(ValueError, match="empty directory"):
        restore(target, restored, password)
    with pytest.raises(ValueError, match="Wrong passphrase"):
        restore(target, tmp_path / "wrong", "a wrong long password")
    assert not (tmp_path / "wrong").exists()
    damaged = bytearray(target.read_bytes())
    damaged[-1] ^= 1
    target.write_bytes(damaged)
    with pytest.raises(ValueError, match="damaged"):
        restore(target, tmp_path / "tampered", password)


def test_backup_rejects_changed_source(tmp_path):
    source = tmp_path / "source"
    sha = fixture_database(source)
    (source / "imports" / f"{sha}.csv").write_bytes(b"Changed")
    with pytest.raises(ValueError, match="checksum"):
        backup(source, tmp_path / "out.fbb", "test passphrase long")


def test_cli_reads_passphrase_from_stdin(tmp_path, monkeypatch):
    import io
    import sys
    from family_budget import cli

    source = tmp_path / "source"
    fixture_database(source)
    target = tmp_path / "out" / "backup.fbb"
    target.parent.mkdir()
    password = "a long test passphrase 123"
    for argv in (
        ["family-budget", "--data-dir", str(source), "backup", str(target), "--passphrase-stdin"],
        ["family-budget", "restore", str(target), str(tmp_path / "restored"), "--passphrase-stdin"],
    ):
        monkeypatch.setattr(sys, "argv", argv)
        monkeypatch.setattr(sys, "stdin", io.StringIO(password + "\n"))
        cli.main()
    assert (tmp_path / "restored" / "budget.sqlite3").exists()
