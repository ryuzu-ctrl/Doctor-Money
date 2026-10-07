import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.database import Base
import backend.database as database
from backend.repository import account_for_telegram, apply_bot_mutation, put_state, read_bot_state, state_of


@pytest.fixture
def sessions():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    engine.dispose()


def test_telegram_accounts_are_isolated(sessions):
    with sessions() as db:
        first = account_for_telegram(db, 1001)
        second = account_for_telegram(db, 1002)
        first_state = state_of(first)
        first_state["name"] = "Dina"
        put_state(db, first, first_state, 0)
        assert state_of(second)["name"] == "Sobat"


def test_state_updates_use_optimistic_revision(sessions):
    with sessions() as db:
        account = account_for_telegram(db, 1003)
        first = state_of(account)
        first["name"] = "Dina"
        put_state(db, account, first, 0)
        with pytest.raises(ValueError, match="revision_conflict"):
            put_state(db, account, first, 0)


def test_bot_mutation_commits_to_shared_store(sessions, monkeypatch):
    monkeypatch.setattr(database, "SessionLocal", sessions)
    monkeypatch.setattr(database, "engine", sessions.kw["bind"])
    with sessions() as db:
        account_for_telegram(db, 1004)
    apply_bot_mutation(1004, lambda state: state.update(name="Telegram"))
    assert read_bot_state(1004)["name"] == "Telegram"