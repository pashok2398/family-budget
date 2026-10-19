from contextlib import contextmanager
from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session
from .config import data_dir


class Base(DeclarativeBase):
    pass


def make_engine(path=None):
    engine = create_engine(
        f"sqlite:///{path or data_dir() / 'budget.sqlite3'}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )

    @event.listens_for(engine, "connect")
    def configure(connection, _):
        connection.isolation_level = None
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=30000")

    @event.listens_for(engine, "begin")
    def begin(connection):
        connection.exec_driver_sql("BEGIN IMMEDIATE" if connection.info.get("write") else "BEGIN")

    return engine


@contextmanager
def session_scope(engine, write=False):
    with engine.connect() as connection:
        connection.info["write"] = write
        try:
            with Session(bind=connection, expire_on_commit=False) as session:
                with session.begin():
                    yield session
        finally:
            connection.info.pop("write", None)
