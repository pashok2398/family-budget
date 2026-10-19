import argparse
import getpass
import json
import os
from pathlib import Path


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description="Family Budget — local finance CLI")
    parser.add_argument("--data-dir", help="Absolute runtime directory outside the source checkout")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init")
    serve = commands.add_parser("serve")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--host", help="Interface address to listen on (also set FAMILY_BUDGET_ALLOWED_HOSTS)")
    serve.add_argument(
        "--container",
        action="store_true",
        help="Listen on container interface (publish only to host loopback)",
    )
    account = commands.add_parser("account")
    account.add_argument("name")
    account.add_argument("--institution", default="hapoalim")
    card = commands.add_parser("card")
    card.add_argument("account_id")
    card.add_argument("name")
    imp = commands.add_parser("import")
    imp.add_argument("file", type=Path)
    imp.add_argument("--account", required=True)
    imp.add_argument("--card")
    imp.add_argument("--adapter", choices=["hapoalim", "isracard"], required=True)
    imp.add_argument("--options", type=Path)
    commit = commands.add_parser("commit")
    commit.add_argument("batch_id")
    review = commands.add_parser("review")
    review.add_argument("--decisions", type=Path, help="JSON {items:[corrections]} to apply")
    resolve = commands.add_parser("resolve")
    resolve.add_argument("row_id")
    resolve.add_argument("resolution")
    backup = commands.add_parser("backup")
    backup.add_argument("destination", type=Path)
    restore = commands.add_parser("restore")
    restore.add_argument("archive", type=Path)
    restore.add_argument("destination", type=Path)
    args = parser.parse_args()
    if args.data_dir:
        os.environ["FAMILY_BUDGET_DATA_DIR"] = args.data_dir
    from .config import data_dir, MAX_UPLOAD

    root = data_dir()
    if args.command == "serve":
        import uvicorn
        from .api.app import create_app

        uvicorn.run(
            create_app(),
            host=args.host or ("0.0.0.0" if args.container else "127.0.0.1"),
            port=args.port,
            access_log=False,
        )
        return
    if args.command in {"backup", "restore"}:
        from .backup import backup, restore

        password = getpass.getpass("Backup passphrase (12+ characters): ")
        if args.command == "backup":
            if password != getpass.getpass("Repeat passphrase: "):
                parser.error("Passphrases differ")
            backup(root, args.destination.expanduser().resolve(), password)
        else:
            restore(args.archive.expanduser().resolve(), args.destination.expanduser().resolve(), password)
        print("Completed")
        return
    from .db import make_engine, session_scope
    from .migrate import upgrade
    from .seed import seed
    from .api.app import serialize
    from .models import Account, CreditCard, Transaction
    from .ingestion.service import preview, commit, resolve_row
    from sqlalchemy import select

    engine = make_engine()
    upgrade(engine)
    with session_scope(engine, write=True) as session:
        seed(session)
        result = {"status": "initialized", "data_dir": str(root)}
        if args.command == "account":
            item = Account(name=args.name, institution=args.institution)
            session.add(item)
            session.flush()
            result = serialize(item)
        elif args.command == "card":
            if not session.get(Account, args.account_id):
                parser.error("Unknown account")
            item = CreditCard(account_id=args.account_id, name=args.name)
            session.add(item)
            session.flush()
            result = serialize(item)
        elif args.command == "import":
            if args.file.stat().st_size > MAX_UPLOAD:
                parser.error("File exceeds 20 MiB")
            result = serialize(
                preview(
                    session,
                    root,
                    args.file.read_bytes(),
                    args.file.name,
                    args.account,
                    args.card,
                    args.adapter,
                    json.loads(args.options.read_text()) if args.options else {},
                )
            )
        elif args.command == "commit":
            result = serialize(commit(session, args.batch_id))
        elif args.command == "resolve":
            result = serialize(resolve_row(session, args.row_id, args.resolution))
        elif args.command == "review":
            if args.decisions:
                from .api.schemas import Corrections
                from .classification.corrections import correct

                result = [
                    serialize(correct(session, x))
                    for x in Corrections.model_validate_json(args.decisions.read_text()).items
                ]
            else:
                result = [
                    serialize(t)
                    for t in session.scalars(
                        select(Transaction).where(Transaction.review_status == "pending")
                    )
                ]
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
