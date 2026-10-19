import pytest

pytest.importorskip("sqlalchemy")
from sqlalchemy import inspect, text
from family_budget.migrate import upgrade
from family_budget.models import Base


def test_schema_wal_foreign_keys_and_repeat_upgrade(ledger):
    engine, *_ = ledger
    upgrade(engine)
    with engine.connect() as c:
        assert c.execute(text("PRAGMA journal_mode")).scalar() == "wal"
        assert c.execute(text("PRAGMA foreign_keys")).scalar() == 1
        assert c.execute(text("PRAGMA integrity_check")).scalar() == "ok"
        assert not c.execute(text("PRAGMA foreign_key_check")).all()
        assert set(Base.metadata.tables) <= set(inspect(c).get_table_names())
        assert c.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0006"
