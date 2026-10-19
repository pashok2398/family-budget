from pathlib import Path
import pytest


@pytest.fixture
def ledger(tmp_path):
    from family_budget.db import make_engine, session_scope
    from family_budget.migrate import upgrade
    from family_budget.seed import seed
    from family_budget.models import Account, CreditCard

    engine = make_engine(tmp_path / "budget.sqlite3")
    upgrade(engine)
    with session_scope(engine, write=True) as session:
        seed(session)
        account = Account(name="Test account", institution="hapoalim")
        session.add(account)
        session.flush()
        card = CreditCard(name="Test card", account_id=account.id)
        session.add(card)
        session.flush()
        ids = account.id, card.id
    yield engine, tmp_path, *ids
    engine.dispose()


@pytest.fixture
def fixture_dir():
    return Path(__file__).parent / "fixtures"
