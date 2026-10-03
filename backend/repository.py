import hashlib
import json
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .models import Account, ApiToken, PairCode
from bot.services.finance import empty_state


def now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def state_of(account: Account) -> dict[str, Any]:
    return json.loads(account.state_json)


def issue_token(db: Session, account: Account) -> str:
    token = secrets.token_urlsafe(32)
    db.add(ApiToken(account_id=account.id, token_hash=hash_secret(token), created_at=now_utc()))
    return token


def new_account(db: Session, telegram_id: int | None = None, name: str = "Sobat", dashboard_linked: bool = False) -> Account:
    account = Account(telegram_id=telegram_id, dashboard_linked=dashboard_linked, state_json=json.dumps(empty_state(name), ensure_ascii=False), revision=0, created_at=now_utc())
    db.add(account)
    db.flush()
    return account


def account_for_telegram(db: Session, telegram_id: int) -> Account:
    account = db.scalar(select(Account).where(Account.telegram_id == telegram_id))
    if account is None:
        account = new_account(db, telegram_id)
        db.commit()
        db.refresh(account)
    return account


def account_for_token(db: Session, token: str) -> Account | None:
    token_row = db.scalar(select(ApiToken).where(ApiToken.token_hash == hash_secret(token)))
    return db.get(Account, token_row.account_id) if token_row else None


def put_state(db: Session, account: Account, state: dict[str, Any], expected_revision: int | None = None) -> int:
    expected = account.revision if expected_revision is None else expected_revision
    if account.revision != expected:
        raise ValueError("revision_conflict")
    result = db.execute(update(Account).where(Account.id == account.id, Account.revision == expected).values(state_json=json.dumps(state, ensure_ascii=False, separators=(",", ":")), revision=expected + 1))
    if result.rowcount != 1:
        db.rollback()
        raise ValueError("revision_conflict")
    db.commit()
    db.refresh(account)
    return account.revision


def make_pair_code(db: Session, telegram_id: int) -> tuple[str, datetime]:
    code = f"{secrets.randbelow(100_000_000):08d}"
    expiry = now_utc() + timedelta(minutes=10)
    db.add(PairCode(code_hash=hash_secret(code), telegram_id=telegram_id, expires_at=expiry))
    db.commit()
    return code, expiry


def use_pair_code(db: Session, account: Account, code: str) -> Account:
    pair = db.scalar(select(PairCode).where(PairCode.code_hash == hash_secret(code), PairCode.consumed_at.is_(None)))
    if pair is None or pair.expires_at < now_utc():
        raise ValueError("Kode tidak valid atau kedaluwarsa")
    linked = db.scalar(select(Account).where(Account.telegram_id == pair.telegram_id))
    if linked and linked.id != account.id:
        local_state = state_of(linked)
        if local_state.get("txs") or local_state.get("goals") or local_state.get("budgets") or local_state.get("reminders") or local_state.get("watchlist") or local_state.get("alerts") or local_state.get("portfolio") or any(int(w.get("start", 0)) for w in local_state.get("wallets", [])):
            raise ValueError("Akun Telegram sudah memiliki data lokal. Ekspor atau hubungkan akun itu terlebih dahulu.")
        linked.telegram_id = None
    if account.telegram_id not in (None, pair.telegram_id):
        raise ValueError("Akun dashboard sudah terhubung ke akun Telegram lain")
    account.telegram_id = pair.telegram_id
    account.dashboard_linked = True
    pair.consumed_at = now_utc()
    db.commit()
    db.refresh(account)
    return account


def apply_bot_mutation(telegram_id: int, mutation) -> tuple[dict[str, Any], Any]:
    from .database import SessionLocal, engine

    with SessionLocal() as db:
        account = db.scalar(select(Account).where(Account.telegram_id == telegram_id))
        if account is None:
            account = account_for_telegram(db, telegram_id)
        if engine.dialect.name == "sqlite":
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            account = db.scalar(select(Account).where(Account.telegram_id == telegram_id))
        else:
            account = db.scalar(select(Account).where(Account.telegram_id == telegram_id).with_for_update())
        if account is None:
            raise ValueError("Akun tidak ditemukan")
        state = state_of(account)
        result = mutation(state)
        account.state_json = json.dumps(state, ensure_ascii=False, separators=(",", ":"))
        account.revision += 1
        db.commit()
        return state, result


def read_bot_state(telegram_id: int) -> dict[str, Any]:
    from .database import SessionLocal

    with SessionLocal() as db:
        return state_of(account_for_telegram(db, telegram_id))