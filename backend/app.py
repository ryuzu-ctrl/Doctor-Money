import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Generator

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .database import Base, SessionLocal, engine
from .models import Account
from .repository import account_for_token, issue_token, make_pair_code, new_account, put_state, state_of, use_pair_code
from .security import InitDataError, validate_init_data
from bot.services.finance import empty_state, prepare_legacy_state


Base.metadata.create_all(bind=engine)
app = FastAPI(title="Doctor Money API", version="1.0.0")
DASHBOARD_FILE = Path(__file__).resolve().parents[1] / "dashboard" / "Doctor Money.html"
LANDING_FILE = Path(__file__).resolve().parents[1] / "dashboard" / "landing.html"
_rate_lock = threading.Lock()
_rate_events: dict[str, list[float]] = {}


def bot_telegram_link() -> str:
    configured = os.getenv("TELEGRAM_BOT_LINK") or os.getenv("BOT_LINK")
    if configured:
        return configured

    username = os.getenv("TELEGRAM_BOT_USERNAME") or os.getenv("BOT_USERNAME") or "DoctorMoneyBot"
    username = username.lstrip("@")
    return f"https://t.me/{username}"


def check_rate_limit(key: str, limit: int = 120, window: int = 60) -> bool:
    now = time.monotonic()
    with _rate_lock:
        events = [stamp for stamp in _rate_events.get(key, []) if now - stamp < window]
        if len(events) >= limit:
            _rate_events[key] = events
            return False
        events.append(now)
        _rate_events[key] = events
        return True


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def current_account(authorization: str | None = Header(default=None), db: Session = Depends(get_db)) -> Account:
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token or len(token) > 128:
        raise HTTPException(status_code=401, detail="Sesi tidak valid")
    account = account_for_token(db, token)
    if account is None:
        raise HTTPException(status_code=401, detail="Sesi tidak valid")
    if not check_rate_limit("account:" + str(account.id)):
        raise HTTPException(status_code=429, detail="Terlalu banyak permintaan. Coba lagi sebentar.")
    return account


class AccountRequest(BaseModel):
    init_data: str | None = Field(default=None, max_length=16_384)


class StateRequest(BaseModel):
    state: dict[str, Any]
    revision: int = Field(ge=0)


class ImportRequest(BaseModel):
    state: dict[str, Any]


class PairRequest(BaseModel):
    code: str = Field(min_length=8, max_length=8, pattern=r"^\d{8}$")


def validate_state(state: dict[str, Any]) -> None:
    if len(json.dumps(state, ensure_ascii=False)) > 2_000_000:
        raise HTTPException(status_code=413, detail="Data terlalu besar")
    if not all(key in state for key in ("txs", "wallets", "budgets", "goals", "name")):
        raise HTTPException(status_code=422, detail="Bentuk data tidak valid")
    if not isinstance(state["txs"], list) or len(state["txs"]) > 100_000:
        raise HTTPException(status_code=422, detail="Daftar transaksi tidak valid")
    if not isinstance(state["wallets"], list) or not state["wallets"] or len(state["wallets"]) > 100:
        raise HTTPException(status_code=422, detail="Setidaknya satu dompet diperlukan")
    if not isinstance(state["budgets"], dict) or not isinstance(state["goals"], list):
        raise HTTPException(status_code=422, detail="Anggaran atau tujuan tidak valid")
    wallet_ids = set()
    for wallet in state["wallets"]:
        if not isinstance(wallet, dict) or not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", str(wallet.get("id", ""))) or wallet["id"] in wallet_ids or len(str(wallet.get("n", ""))) > 80:
            raise HTTPException(status_code=422, detail="Ada dompet dengan data tidak valid")
        wallet_ids.add(wallet["id"])
        try:
            if int(wallet.get("start", 0)) < 0:
                raise ValueError
        except (TypeError, ValueError):
            raise HTTPException(status_code=422, detail="Saldo awal tidak valid")
    for tx in state["txs"]:
        try:
            valid = isinstance(tx, dict) and bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(tx.get("date", "")))) and 0 < int(tx.get("amt", 0)) <= 10**15 and tx.get("type") in {"in", "out"} and tx.get("w") in wallet_ids and len(str(tx.get("desc", ""))) <= 120
        except (TypeError, ValueError):
            valid = False
        if not valid:
            raise HTTPException(status_code=422, detail="Ada transaksi dengan data tidak valid")
    try:
        if any(int(value) < 0 or int(value) > 10**15 for value in state["budgets"].values()):
            raise ValueError
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="Batas anggaran tidak valid")
    if len(str(state.get("name", ""))) > 40:
        raise HTTPException(status_code=422, detail="Nama terlalu panjang")


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.middleware("http")
async def limit_account_creation(request: Request, call_next):
    if request.url.path == "/api/accounts":
        client_ip = request.client.host if request.client else "unknown"
        if not check_rate_limit("ip:" + client_ip, limit=20):
            return JSONResponse({"detail": "Terlalu banyak pembuatan akun. Coba lagi sebentar."}, status_code=429)
    return await call_next(request)


@app.post("/api/accounts")
def create_browser_account(payload: AccountRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    telegram_id = None
    name = "Sobat"
    if payload.init_data:
        try:
            user = validate_init_data(payload.init_data)
            telegram_id = int(user["id"])
            name = str(user.get("first_name") or name)[:40]
        except InitDataError as exc:
            raise HTTPException(status_code=401, detail="Identitas Telegram tidak valid") from exc
    account = db.scalar(select(Account).where(Account.telegram_id == telegram_id)) if telegram_id is not None else None
    if account is None:
        account = new_account(db, telegram_id, name, dashboard_linked=telegram_id is not None)
    elif telegram_id is not None:
        account.dashboard_linked = True
    token = issue_token(db, account)
    db.commit()
    return {"token": token, "state": state_of(account), "revision": account.revision}


@app.get("/api/state")
def get_state(account: Account = Depends(current_account)) -> dict[str, Any]:
    return {"state": state_of(account), "revision": account.revision}


@app.put("/api/state")
def save_state(payload: StateRequest, db: Session = Depends(get_db), account: Account = Depends(current_account)) -> dict[str, int]:
    validate_state(payload.state)
    try:
        revision = put_state(db, account, payload.state, payload.revision)
    except ValueError as exc:
        if str(exc) == "revision_conflict":
            raise HTTPException(status_code=409, detail="Data berubah di perangkat lain. Muat ulang untuk menyelaraskan.") from exc
        raise
    return {"revision": revision}


@app.post("/api/import/preview")
def import_preview(legacy: dict[str, Any], account: Account = Depends(current_account)) -> dict[str, Any]:
    prepared, skipped = prepare_legacy_state(legacy)
    return {"state": prepared, "account_revision": account.revision, "imported_transactions": len(prepared["txs"]), "skipped_sample_items": skipped, "wallets": len(prepared["wallets"]), "budgets": len(prepared["budgets"]), "goals": len(prepared["goals"])}


@app.post("/api/import/confirm")
def import_confirm(payload: ImportRequest, db: Session = Depends(get_db), account: Account = Depends(current_account)) -> dict[str, int]:
    state, _ = prepare_legacy_state(payload.state)
    validate_state(state)
    if account.revision or state_of(account).get("txs"):
        raise HTTPException(status_code=409, detail="Akun sudah menerima data baru; impor dibatalkan agar tidak menimpa data.")
    return {"revision": put_state(db, account, state, 0)}


@app.post("/api/link")
def link_telegram(payload: PairRequest, db: Session = Depends(get_db), account: Account = Depends(current_account)) -> dict[str, str]:
    try:
        use_pair_code(db, account, payload.code)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"status": "terhubung"}


@app.get("/api/link/status")
def link_status(account: Account = Depends(current_account)) -> dict[str, Any]:
    return {"connected": account.dashboard_linked, "telegram_id": account.telegram_id if account.dashboard_linked else None}


@app.delete("/api/link")
def unlink_telegram(db: Session = Depends(get_db), account: Account = Depends(current_account)) -> dict[str, str]:
    telegram_id = account.telegram_id
    account.dashboard_linked = False
    account.telegram_id = None
    if telegram_id is not None:
        db.flush()
        new_account(db, telegram_id, name=state_of(account).get("name", "Sobat"))
    db.commit()
    return {"status": "terputus"}


@app.get("/api/link/code/{telegram_id}")
def create_pair_code(telegram_id: int, db: Session = Depends(get_db), authorization: str | None = Header(default=None)) -> dict[str, Any]:
    expected = os.getenv("APP_SECRET", "")
    scheme, _, secret = (authorization or "").partition(" ")
    import hmac
    if not expected or scheme.lower() != "bearer" or not hmac.compare_digest(secret, expected):
        raise HTTPException(status_code=401, detail="Tidak diizinkan")
    code, expiry = make_pair_code(db, telegram_id)
    return {"code": code, "expires_at": expiry.isoformat()}


@app.get("/")
def landing_page() -> HTMLResponse:
    template_path = LANDING_FILE if LANDING_FILE.exists() else DASHBOARD_FILE
    content = template_path.read_text(encoding="utf-8")
    content = content.replace("{{TELEGRAM_LINK}}", bot_telegram_link())
    return HTMLResponse(content=content, media_type="text/html")


@app.get("/dashboard")
def dashboard_page() -> FileResponse:
    return FileResponse(DASHBOARD_FILE, media_type="text/html")


if os.getenv("BOT_TOKEN"):
    @app.get("/api/config")
    def public_config() -> dict[str, bool]:
        return {"telegram_web_app": True}