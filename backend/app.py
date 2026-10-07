import base64
import asyncio
import binascii
import hmac
import json
import logging
import os
import re
import secrets
import smtplib
import ssl
import threading
import time
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Generator, Literal

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
import httpx

from .database import Base, SessionLocal, engine, migrate_auth_schema
from .models import Account, PasswordReset, ProAccess, ProOrder
from .pro import PRO_PLANS, activate_pro_plan, ensure_pro_access, pro_access_active, pro_payment_config
from .repository import account_for_token, hash_password, hash_secret, issue_token, make_pair_code, new_account, now_utc, put_state, revoke_account_tokens, revoke_token, state_of, use_pair_code, verify_password
from .security import InitDataError, validate_init_data
from . import whatsapp
from bot.services import market
from bot.services.finance import prepare_legacy_state


Base.metadata.create_all(bind=engine)
migrate_auth_schema()
if engine.dialect.name == "postgresql":
    with engine.begin() as connection:
        connection.exec_driver_sql('ALTER TABLE "pro_access" ENABLE ROW LEVEL SECURITY')
        connection.exec_driver_sql('ALTER TABLE "pro_orders" ENABLE ROW LEVEL SECURITY')
        connection.exec_driver_sql('ALTER TABLE "payments" ENABLE ROW LEVEL SECURITY')
app = FastAPI(title="Doctor Money API", version="1.0.0")
logger = logging.getLogger("doctor_money.auth")
webapp_url = os.getenv("WEBAPP_URL", "").strip().rstrip("/")
if webapp_url:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[webapp_url],
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
    )
FRONTEND_DIR = Path(__file__).resolve().parents[1] / "frontend"
DASHBOARD_FILE = FRONTEND_DIR / "dashboard.html"
LANDING_FILE = FRONTEND_DIR / "index.html"
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


class EmailPasswordRequest(BaseModel):
    email: str = Field(min_length=1, max_length=320)
    password: str = Field(min_length=1, max_length=128)


class PasswordResetRequest(BaseModel):
    email: str = Field(min_length=1, max_length=320)
    method: Literal["email", "telegram"]


class PasswordResetConfirmRequest(PasswordResetRequest):
    code: str = Field(min_length=8, max_length=8, pattern=r"^\d{8}$")
    new_password: str = Field(min_length=12, max_length=128)


class ProfileUpdateRequest(BaseModel):
    name: str | None = Field(default=None, max_length=40)
    email: str | None = Field(default=None, max_length=320)
    avatar: str | None = Field(default=None, max_length=200_000)
    current_password: str | None = Field(default=None, max_length=128)


class PasswordChangeRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=12, max_length=128)


class StateRequest(BaseModel):
    state: dict[str, Any]
    revision: int = Field(ge=0)


class ImportRequest(BaseModel):
    state: dict[str, Any]


class PairRequest(BaseModel):
    code: str = Field(min_length=8, max_length=8, pattern=r"^\d{8}$")


class ProOrderRequest(BaseModel):
    plan_id: Literal["monthly", "six_months", "annual"]


DUMMY_PASSWORD_HASH = "scrypt$32768$8$1$MDAwMDAwMDAwMDAwMDAwMA==$" + base64.urlsafe_b64encode(bytes(64)).decode("ascii")


def iso_utc(value: datetime | None) -> str | None:
    return value.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z") if value else None


def plan_payload(plan_id: str) -> dict[str, Any]:
    return {"id": plan_id, **PRO_PLANS[plan_id]}


def order_payload(order: ProOrder) -> dict[str, Any]:
    return {
        "order_id": order.id,
        "plan": plan_payload(order.plan_id),
        "amount": order.amount,
        "status": order.status,
        "created_at": iso_utc(order.created_at),
        "payment": pro_payment_config(),
        "support_url": os.getenv("SUPPORT_URL") or bot_telegram_link(),
    }


def require_pro(account: Account = Depends(current_account), db: Session = Depends(get_db)) -> Account:
    access = ensure_pro_access(db, account)
    if not pro_access_active(access):
        raise HTTPException(status_code=402, detail="Masa Pro Anda telah berakhir. Pilih paket untuk mengaktifkan kembali fitur dashboard.")
    return account


def require_pro_admin(authorization: str | None) -> None:
    expected = os.getenv("PRO_ADMIN_SECRET", "")
    scheme, _, secret = (authorization or "").partition(" ")
    if not expected:
        raise HTTPException(status_code=503, detail="PRO_ADMIN_SECRET belum dikonfigurasi")
    if scheme.lower() != "bearer" or not hmac.compare_digest(secret, expected):
        raise HTTPException(status_code=401, detail="Tidak diizinkan")


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


@app.get("/api/pro/status")
def pro_status(db: Session = Depends(get_db), account: Account = Depends(current_account)) -> dict[str, Any]:
    access = ensure_pro_access(db, account)
    pending = db.scalar(select(ProOrder).where(ProOrder.account_id == account.id, ProOrder.status == "pending").order_by(ProOrder.created_at.desc()))
    return {
        "status": access.status,
        "active": pro_access_active(access),
        "plan": plan_payload(access.plan_id) if access.plan_id in PRO_PLANS else None,
        "trial_started_at": iso_utc(access.trial_started_at),
        "trial_ends_at": iso_utc(access.trial_ends_at),
        "starts_at": iso_utc(access.starts_at),
        "expires_at": iso_utc(access.expires_at),
        "plans": [plan_payload(plan_id) for plan_id in PRO_PLANS],
        "payment": pro_payment_config(),
        "pending_order": order_payload(pending) if pending else None,
        "support_url": os.getenv("SUPPORT_URL") or bot_telegram_link(),
    }


@app.post("/api/pro/orders")
def create_pro_order(payload: ProOrderRequest, db: Session = Depends(get_db), account: Account = Depends(current_account)) -> dict[str, Any]:
    pending = db.scalar(select(ProOrder).where(ProOrder.account_id == account.id, ProOrder.plan_id == payload.plan_id, ProOrder.status == "pending").order_by(ProOrder.created_at.desc()))
    if pending:
        return order_payload(pending)
    plan = PRO_PLANS[payload.plan_id]
    order = ProOrder(id=secrets.token_hex(12).upper(), account_id=account.id, plan_id=payload.plan_id, amount=plan["price"], status="pending", created_at=now_utc())
    db.add(order)
    db.commit()
    db.refresh(order)
    return order_payload(order)


@app.get("/api/admin/pro/orders")
def pending_pro_orders(authorization: str | None = Header(default=None), db: Session = Depends(get_db)) -> dict[str, Any]:
    require_pro_admin(authorization)
    orders = db.scalars(select(ProOrder).where(ProOrder.status == "pending").order_by(ProOrder.created_at)).all()
    return {"orders": [{"order_id": order.id, "account_id": order.account_id, "plan": plan_payload(order.plan_id), "amount": order.amount, "created_at": iso_utc(order.created_at)} for order in orders]}


@app.post("/api/admin/pro/orders/{order_id}/activate")
def activate_pro_order(order_id: str, authorization: str | None = Header(default=None), db: Session = Depends(get_db)) -> dict[str, Any]:
    require_pro_admin(authorization)
    order = db.get(ProOrder, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Order tidak ditemukan")
    if order.status == "paid":
        access = db.get(ProAccess, order.account_id)
        return {"status": "paid", "expires_at": iso_utc(access.expires_at if access else None)}
    if order.status != "pending":
        raise HTTPException(status_code=409, detail="Order tidak dapat diaktifkan")

    access = activate_pro_plan(db, db.get(Account, order.account_id), order.plan_id)
    now = now_utc()
    order.status = "paid"
    order.paid_at = now
    db.commit()
    return {"status": order.status, "order_id": order.id, "account_id": order.account_id, "plan": plan_payload(order.plan_id), "expires_at": iso_utc(access.expires_at)}


@app.middleware("http")
async def limit_account_creation(request: Request, call_next):
    if request.url.path == "/api/accounts":
        client_ip = request.client.host if request.client else "unknown"
        if not check_rate_limit("ip:" + client_ip, limit=20):
            return JSONResponse({"detail": "Terlalu banyak pembuatan akun. Coba lagi sebentar."}, status_code=429)
    if request.url.path in {"/api/auth/login", "/api/auth/signup"}:
        client_ip = request.client.host if request.client else "unknown"
        if not check_rate_limit("auth:" + client_ip, limit=10, window=300):
            return JSONResponse({"detail": "Terlalu banyak percobaan autentikasi. Coba lagi dalam beberapa menit."}, status_code=429)
    if request.url.path in {"/api/auth/password-reset/request", "/api/auth/password-reset/confirm"}:
        client_ip = request.client.host if request.client else "unknown"
        if not check_rate_limit("password-reset:" + client_ip, limit=5, window=3600):
            return JSONResponse({"detail": "Terlalu banyak permintaan reset. Coba lagi dalam satu jam."}, status_code=429)
    return await call_next(request)


def normalized_email(value: str) -> str:
    email = value.strip().casefold()
    if len(email) > 320 or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise HTTPException(status_code=422, detail="Alamat email tidak valid")
    return email


def auth_response(db: Session, account: Account) -> dict[str, Any]:
    token = issue_token(db, account)
    db.commit()
    return {"token": token, "email": account.email, "state": state_of(account), "revision": account.revision}


def send_reset_email(email: str, code: str) -> None:
    host = os.getenv("SMTP_HOST", "").strip()
    sender = os.getenv("SMTP_FROM", "").strip()
    if not host or not sender:
        raise RuntimeError("SMTP_HOST dan SMTP_FROM belum dikonfigurasi")
    port = int(os.getenv("SMTP_PORT", "587"))
    username = os.getenv("SMTP_USERNAME", "").strip()
    password = os.getenv("SMTP_PASSWORD", "")
    use_ssl = os.getenv("SMTP_USE_SSL", "false").strip().lower() == "true"
    use_starttls = os.getenv("SMTP_STARTTLS", "true").strip().lower() == "true"
    if username and not password:
        raise RuntimeError("SMTP_PASSWORD belum dikonfigurasi")

    message = EmailMessage()
    message["Subject"] = "Kode reset kata sandi Doctor Money"
    message["From"] = sender
    message["To"] = email
    message.set_content(
        f"Kode reset kata sandi Anda: {code}\n\n"
        "Kode berlaku selama 10 menit dan hanya dapat digunakan sekali. "
        "Jika Anda tidak meminta reset, abaikan email ini."
    )

    if use_ssl:
        with smtplib.SMTP_SSL(host, port, timeout=10, context=ssl.create_default_context()) as server:
            if username:
                server.login(username, password)
            server.send_message(message)
    else:
        with smtplib.SMTP(host, port, timeout=10) as server:
            server.ehlo()
            if use_starttls:
                server.starttls(context=ssl.create_default_context())
                server.ehlo()
            if username:
                server.login(username, password)
            server.send_message(message)


async def send_reset_telegram(telegram_id: int, code: str) -> None:
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("BOT_TOKEN belum dikonfigurasi")
    async with httpx.AsyncClient(timeout=httpx.Timeout(8.0)) as client:
        response = await client.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={
                "chat_id": telegram_id,
                "text": f"Kode reset kata sandi Doctor Money: {code}\n\nKode berlaku 10 menit dan hanya bisa digunakan sekali. Jangan bagikan kode ini.",
            },
        )
        response.raise_for_status()
        result = response.json()
        if not result.get("ok"):
            raise RuntimeError("Telegram menolak pengiriman kode reset")


def create_password_reset(db: Session, account: Account, method: str) -> tuple[PasswordReset, str]:
    now = now_utc()
    db.query(PasswordReset).filter(
        PasswordReset.account_id == account.id,
        PasswordReset.method == method,
        PasswordReset.consumed_at.is_(None),
    ).update({"consumed_at": now}, synchronize_session=False)
    code = f"{secrets.randbelow(100_000_000):08d}"
    challenge = PasswordReset(
        account_id=account.id,
        method=method,
        code_hash=hash_secret(code),
        telegram_id=account.telegram_id if method == "telegram" else None,
        attempts=0,
        created_at=now,
        expires_at=now + timedelta(minutes=10),
    )
    db.add(challenge)
    db.commit()
    db.refresh(challenge)
    return challenge, code


@app.post("/api/auth/signup")
def signup(payload: EmailPasswordRequest, authorization: str | None = Header(default=None), db: Session = Depends(get_db)) -> dict[str, Any]:
    email = normalized_email(payload.email)
    if len(payload.password) < 12:
        raise HTTPException(status_code=422, detail="Kata sandi harus memiliki minimal 12 karakter")
    if db.scalar(select(Account.id).where(Account.email == email)) is not None:
        raise HTTPException(status_code=409, detail="Email sudah terdaftar. Silakan masuk.")

    account = None
    if authorization:
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token or len(token) > 128:
            raise HTTPException(status_code=401, detail="Sesi tidak valid")
        account = account_for_token(db, token)
        if account is None:
            raise HTTPException(status_code=401, detail="Sesi tidak valid")
        if account.email is not None:
            raise HTTPException(status_code=409, detail="Akun ini sudah memiliki email. Silakan masuk.")
    if account is None:
        account = new_account(db)
    else:
        revoke_account_tokens(db, account.id)
    account.email = email
    account.password_hash = hash_password(payload.password)
    try:
        return auth_response(db, account)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Email sudah terdaftar. Silakan masuk.") from exc


@app.post("/api/auth/login")
def login(payload: EmailPasswordRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    email = normalized_email(payload.email)
    account = db.scalar(select(Account).where(Account.email == email))
    if not verify_password(payload.password, account.password_hash if account else DUMMY_PASSWORD_HASH):
        raise HTTPException(status_code=401, detail="Email atau kata sandi salah")
    return auth_response(db, account)


@app.post("/api/auth/password-reset/request")
async def request_password_reset(payload: PasswordResetRequest, request: Request, db: Session = Depends(get_db)) -> dict[str, str]:
    email = normalized_email(payload.email)
    if payload.method == "email":
        if not os.getenv("SMTP_HOST", "").strip() or not os.getenv("SMTP_FROM", "").strip():
            raise HTTPException(status_code=503, detail="Layanan verifikasi email belum dikonfigurasi")
        if os.getenv("SMTP_USERNAME", "").strip() and not os.getenv("SMTP_PASSWORD", ""):
            raise HTTPException(status_code=503, detail="Layanan verifikasi email belum dikonfigurasi")
    elif not os.getenv("BOT_TOKEN", "").strip():
        raise HTTPException(status_code=503, detail="Layanan verifikasi Telegram belum dikonfigurasi")

    account = db.scalar(select(Account).where(Account.email == email))
    if account is None or (payload.method == "telegram" and (not account.dashboard_linked or not account.telegram_id)):
        return {"message": "Jika akun dan metode verifikasi tersedia, kode akan dikirim. Periksa email atau chat bot Telegram."}

    recent_requests = db.query(PasswordReset.id).filter(
        PasswordReset.account_id == account.id,
        PasswordReset.method == payload.method,
        PasswordReset.created_at >= now_utc() - timedelta(hours=1),
    ).count()
    if recent_requests >= 3:
        return {"message": "Jika akun dan metode verifikasi tersedia, kode akan dikirim. Periksa email atau chat bot Telegram."}

    challenge, code = create_password_reset(db, account, payload.method)
    try:
        if payload.method == "email":
            await asyncio.to_thread(send_reset_email, email, code)
        else:
            await send_reset_telegram(challenge.telegram_id, code)
    except (OSError, RuntimeError, ValueError, smtplib.SMTPException, httpx.HTTPError) as exc:
        challenge.consumed_at = now_utc()
        db.commit()
        logger.warning("Pengiriman kode reset gagal", extra={"method": payload.method, "account_id": account.id}, exc_info=True)
        raise HTTPException(status_code=503, detail="Kode verifikasi tidak dapat dikirim. Coba lagi nanti.") from exc
    return {"message": "Jika akun dan metode verifikasi tersedia, kode akan dikirim. Periksa email atau chat bot Telegram."}


@app.post("/api/auth/password-reset/confirm")
def confirm_password_reset(payload: PasswordResetConfirmRequest, db: Session = Depends(get_db)) -> dict[str, str]:
    email = normalized_email(payload.email)
    account = db.scalar(select(Account).where(Account.email == email))
    if account is None:
        raise HTTPException(status_code=400, detail="Kode reset tidak valid atau kedaluwarsa")

    challenge = db.scalar(
        select(PasswordReset)
        .where(
            PasswordReset.account_id == account.id,
            PasswordReset.method == payload.method,
            PasswordReset.consumed_at.is_(None),
            PasswordReset.expires_at > now_utc(),
        )
        .order_by(PasswordReset.created_at.desc())
        .with_for_update()
    )
    if challenge is None:
        raise HTTPException(status_code=400, detail="Kode reset tidak valid atau kedaluwarsa")
    if challenge.attempts >= 5:
        challenge.consumed_at = now_utc()
        db.commit()
        raise HTTPException(status_code=429, detail="Batas percobaan kode tercapai. Minta kode baru.")

    valid_telegram = payload.method != "telegram" or (
        challenge.telegram_id is not None
        and account.telegram_id == challenge.telegram_id
        and account.dashboard_linked
    )
    if not valid_telegram or not hmac.compare_digest(challenge.code_hash, hash_secret(payload.code)):
        challenge.attempts += 1
        if challenge.attempts >= 5:
            challenge.consumed_at = now_utc()
        db.commit()
        raise HTTPException(status_code=400, detail="Kode reset tidak valid atau kedaluwarsa")

    account.password_hash = hash_password(payload.new_password)
    challenge.consumed_at = now_utc()
    revoke_account_tokens(db, account.id)
    db.commit()
    return {"status": "password_reset"}


@app.get("/api/auth/me")
def auth_me(account: Account = Depends(current_account)) -> dict[str, Any]:
    return {"email": account.email, "telegram_linked": account.telegram_id is not None}


AVATAR_SIGNATURES = {"jpeg": (b"\xff\xd8\xff",), "png": (b"\x89PNG\r\n\x1a\n",), "webp": (b"RIFF",)}


def validated_avatar(value: str) -> str | None:
    if not value:
        return None
    match = re.fullmatch(r"data:image/(jpeg|png|webp);base64,([A-Za-z0-9+/]+={0,2})", value)
    try:
        raw = base64.b64decode(match.group(2), validate=True) if match else b""
    except (binascii.Error, ValueError):
        raw = b""
    if not match or not raw.startswith(AVATAR_SIGNATURES[match.group(1)]) or (match.group(1) == "webp" and raw[8:12] != b"WEBP"):
        raise HTTPException(status_code=422, detail="Foto profil harus berupa gambar JPEG, PNG, atau WebP")
    return value


def profile_payload(account: Account) -> dict[str, Any]:
    return {
        "email": account.email,
        "name": str(state_of(account).get("name") or "Sobat"),
        "avatar": account.avatar,
        "has_password": account.password_hash is not None,
        "telegram_linked": account.telegram_id is not None and account.dashboard_linked,
        "whatsapp_number": account.whatsapp_number,
        "created_at": iso_utc(account.created_at),
        "revision": account.revision,
    }


def require_current_password(account: Account, password: str | None) -> None:
    # Only wrong guesses count: five within 15 minutes lock the check for this account.
    key = "password-check:" + str(account.id)
    now = time.monotonic()
    with _rate_lock:
        failures = [stamp for stamp in _rate_events.get(key, []) if now - stamp < 900]
        _rate_events[key] = failures
    if len(failures) >= 5:
        raise HTTPException(status_code=429, detail="Terlalu banyak percobaan kata sandi. Coba lagi dalam 15 menit.")
    if not verify_password(password or "", account.password_hash):
        with _rate_lock:
            _rate_events.setdefault(key, []).append(now)
        raise HTTPException(status_code=403, detail="Kata sandi saat ini salah")


@app.get("/api/auth/profile")
def get_profile(account: Account = Depends(current_account)) -> dict[str, Any]:
    return profile_payload(account)


@app.put("/api/auth/profile")
def update_profile(payload: ProfileUpdateRequest, db: Session = Depends(get_db), account: Account = Depends(current_account)) -> dict[str, Any]:
    if payload.email is not None:
        email = normalized_email(payload.email)
        if email != account.email:
            if account.email is None or account.password_hash is None:
                raise HTTPException(status_code=409, detail="Akun ini belum memiliki login email. Tambahkan email dan kata sandi terlebih dahulu.")
            require_current_password(account, payload.current_password)
            if db.scalar(select(Account.id).where(Account.email == email)) is not None:
                raise HTTPException(status_code=409, detail="Email sudah digunakan akun lain")
            account.email = email
    if payload.avatar is not None:
        account.avatar = validated_avatar(payload.avatar)
    if payload.name is not None:
        name = " ".join(payload.name.split())
        if not name or not name.isprintable():
            raise HTTPException(status_code=422, detail="Nama tidak valid")
        state = state_of(account)
        if state.get("name") != name:
            state["name"] = name
            account.state_json = json.dumps(state, ensure_ascii=False, separators=(",", ":"))
            account.revision += 1
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Email sudah digunakan akun lain") from exc
    return profile_payload(account)


@app.post("/api/auth/password")
def change_password(payload: PasswordChangeRequest, db: Session = Depends(get_db), account: Account = Depends(current_account)) -> dict[str, str]:
    if account.password_hash is None:
        raise HTTPException(status_code=409, detail="Akun ini belum memiliki kata sandi. Tambahkan login email terlebih dahulu.")
    require_current_password(account, payload.current_password)
    if payload.new_password == payload.current_password:
        raise HTTPException(status_code=422, detail="Kata sandi baru harus berbeda dari kata sandi lama")
    account.password_hash = hash_password(payload.new_password)
    # Sign out every session, then hand this one a fresh token.
    revoke_account_tokens(db, account.id)
    token = issue_token(db, account)
    db.commit()
    return {"token": token}


@app.post("/api/auth/logout")
def logout(authorization: str | None = Header(default=None), db: Session = Depends(get_db), account: Account = Depends(current_account)) -> dict[str, str]:
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="Sesi tidak valid")
    revoke_token(db, token, account.id)
    return {"status": "signed_out"}


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


@app.get("/api/market/assets")
async def market_assets() -> dict[str, Any]:
    try:
        return await market.yahoo_market_data()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="Data pasar Yahoo Finance sedang tidak tersedia. Coba muat ulang nanti.") from exc


@app.get("/api/state")
def get_state(account: Account = Depends(require_pro)) -> dict[str, Any]:
    return {"state": state_of(account), "revision": account.revision}


@app.put("/api/state")
def save_state(payload: StateRequest, db: Session = Depends(get_db), account: Account = Depends(require_pro)) -> dict[str, int]:
    validate_state(payload.state)
    try:
        revision = put_state(db, account, payload.state, payload.revision)
    except ValueError as exc:
        if str(exc) == "revision_conflict":
            raise HTTPException(status_code=409, detail="Data berubah di perangkat lain. Muat ulang untuk menyelaraskan.") from exc
        raise
    return {"revision": revision}


@app.post("/api/import/preview")
def import_preview(legacy: dict[str, Any], account: Account = Depends(require_pro)) -> dict[str, Any]:
    prepared, skipped = prepare_legacy_state(legacy)
    return {"state": prepared, "account_revision": account.revision, "imported_transactions": len(prepared["txs"]), "skipped_sample_items": skipped, "wallets": len(prepared["wallets"]), "budgets": len(prepared["budgets"]), "goals": len(prepared["goals"])}


@app.post("/api/import/confirm")
def import_confirm(payload: ImportRequest, db: Session = Depends(get_db), account: Account = Depends(require_pro)) -> dict[str, int]:
    state, _ = prepare_legacy_state(payload.state)
    validate_state(state)
    if account.revision or state_of(account).get("txs"):
        raise HTTPException(status_code=409, detail="Akun sudah menerima data baru; impor dibatalkan agar tidak menimpa data.")
    return {"revision": put_state(db, account, state, 0)}


@app.post("/api/link")
def link_telegram(payload: PairRequest, db: Session = Depends(get_db), account: Account = Depends(require_pro)) -> dict[str, str]:
    if not account.email:
        raise HTTPException(status_code=403, detail="Buat akun email atau masuk sebelum menghubungkan Telegram")
    try:
        use_pair_code(db, account, payload.code)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"status": "terhubung"}


@app.get("/api/link/status")
def link_status(account: Account = Depends(require_pro)) -> dict[str, Any]:
    return {"connected": account.dashboard_linked, "telegram_id": account.telegram_id if account.dashboard_linked else None}


@app.delete("/api/link")
def unlink_telegram(db: Session = Depends(get_db), account: Account = Depends(require_pro)) -> dict[str, str]:
    telegram_id = account.telegram_id
    account.dashboard_linked = False
    account.telegram_id = None
    if telegram_id is not None:
        db.flush()
        new_account(db, telegram_id, name=state_of(account).get("name", "Sobat"))
    db.commit()
    return {"status": "terputus"}


@app.get("/api/whatsapp/status")
def whatsapp_status(account: Account = Depends(require_pro)) -> dict[str, Any]:
    return {"configured": whatsapp.configured(), "connected": account.whatsapp_number is not None, "number": account.whatsapp_number, "bot_number": whatsapp.bot_number()}


@app.post("/api/whatsapp/link-code")
def whatsapp_link_code(db: Session = Depends(get_db), account: Account = Depends(require_pro)) -> dict[str, Any]:
    if not account.email:
        raise HTTPException(status_code=403, detail="Buat akun email atau masuk sebelum menghubungkan WhatsApp")
    if not whatsapp.configured():
        raise HTTPException(status_code=503, detail="Layanan WhatsApp belum dikonfigurasi")
    if not check_rate_limit("wa-code:" + str(account.id), limit=5, window=3600):
        raise HTTPException(status_code=429, detail="Terlalu banyak permintaan kode. Coba lagi nanti.")
    code, expiry = whatsapp.make_link_code(db, account)
    return {"code": code, "expires_at": iso_utc(expiry), "bot_number": whatsapp.bot_number()}


@app.delete("/api/whatsapp/link")
def unlink_whatsapp(db: Session = Depends(get_db), account: Account = Depends(require_pro)) -> dict[str, str]:
    account.whatsapp_number = None
    db.commit()
    return {"status": "terputus"}


@app.post("/api/whatsapp/webhook")
def whatsapp_webhook(payload: dict[str, Any], background: BackgroundTasks, signature: str | None = Header(default=None, alias="X-Webhook-Signature"), db: Session = Depends(get_db)) -> dict[str, str]:
    expected = os.getenv("WASENDER_WEBHOOK_SECRET", "").strip()
    if not whatsapp.configured():
        raise HTTPException(status_code=503, detail="Layanan WhatsApp belum dikonfigurasi")
    if not hmac.compare_digest((signature or "").encode(), expected.encode()):
        raise HTTPException(status_code=401, detail="Tidak diizinkan")
    incoming = whatsapp.incoming_message(payload)
    if incoming is None:
        return {"status": "ignored"}
    number, text, message_id = incoming
    if message_id and not check_rate_limit("wa-message:" + message_id, limit=1, window=3600):
        return {"status": "duplicate"}
    if not check_rate_limit("wa:" + number, limit=20, window=60):
        return {"status": "rate_limited"}

    command = whatsapp.parse_message(text)
    account = db.scalar(select(Account).where(Account.whatsapp_number == number))
    if command["kind"] == "link":
        if not check_rate_limit("wa-link:" + number, limit=5, window=3600):
            reply = "Terlalu banyak percobaan kode. Coba lagi dalam satu jam."
        else:
            reply = whatsapp.link_number(db, number, command["code"])
    elif account is None:
        if not check_rate_limit("wa-unlinked:" + number, limit=2, window=3600):
            return {"status": "ignored"}
        reply = whatsapp.UNLINKED_TEXT
    else:
        reply = whatsapp.reply_for(account.id, command)
    background.add_task(whatsapp.send_text, number, reply)
    return {"status": "ok"}


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
    content = LANDING_FILE.read_text(encoding="utf-8")
    content = content.replace("{{TELEGRAM_LINK}}", bot_telegram_link())
    return HTMLResponse(content=content, media_type="text/html")


@app.get("/dashboard")
def dashboard_page() -> FileResponse:
    return FileResponse(DASHBOARD_FILE, media_type="text/html")


if os.getenv("BOT_TOKEN"):
    @app.get("/api/config")
    def public_config() -> dict[str, bool]:
        return {"telegram_web_app": True}