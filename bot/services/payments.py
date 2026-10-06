import os
import secrets
from datetime import timedelta

import httpx
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from backend.models import Account, Payment
from backend.pro import PRO_PLANS, activate_pro_plan
from backend.repository import now_utc


PAYMENT_WINDOW = timedelta(hours=24)
OPEN_STATUSES = ("waiting_proof", "pending_review")


def open_payment(db: Session, account_id: int) -> Payment | None:
    """The payment the user is still working on: awaiting proof (not yet expired) or awaiting review."""
    now = now_utc()
    rows = db.scalars(select(Payment).where(Payment.account_id == account_id, Payment.status.in_(OPEN_STATUSES)).order_by(Payment.created_at.desc())).all()
    return next((row for row in rows if row.status == "pending_review" or row.expires_at > now), None)


def unique_amount(db: Session, price: int) -> int:
    # 3 random digits (001-499) make each transfer identifiable in the bank statement.
    taken = set(db.scalars(select(Payment.amount).where(Payment.status.in_(OPEN_STATUSES), Payment.amount.between(price + 1, price + 499))).all())
    free = [price + code for code in range(1, 500) if price + code not in taken]
    if not free:
        raise ValueError("Nominal unik habis")
    return secrets.choice(free)


def create_payment(db: Session, account: Account, telegram_id: int, plan_id: str) -> Payment:
    now = now_utc()
    db.execute(update(Payment).where(Payment.account_id == account.id, Payment.status == "waiting_proof").values(status="cancelled"))
    payment = Payment(account_id=account.id, telegram_id=telegram_id, plan_id=plan_id, amount=unique_amount(db, PRO_PLANS[plan_id]["price"]), status="waiting_proof", created_at=now, expires_at=now + PAYMENT_WINDOW)
    db.add(payment)
    db.commit()
    db.refresh(payment)
    return payment


def cancel_payment(db: Session, account_id: int, payment_id: int) -> bool:
    result = db.execute(update(Payment).where(Payment.id == payment_id, Payment.account_id == account_id, Payment.status == "waiting_proof").values(status="cancelled"))
    db.commit()
    return result.rowcount == 1


def attach_proof(db: Session, payment_id: int, proof_path: str) -> bool:
    result = db.execute(update(Payment).where(Payment.id == payment_id, Payment.status == "waiting_proof").values(status="pending_review", proof_path=proof_path, proof_at=now_utc()))
    db.commit()
    return result.rowcount == 1


def settle_payments(db: Session) -> list[tuple[Payment, object]]:
    """Apply the admin's decisions and expire unpaid payments. Returns (payment, pro_access | None) to notify."""
    now = now_utc()
    settled: list[tuple[Payment, object]] = []
    # processed_at marks decisions the bot has already acted on, so each approval extends Pro exactly once.
    for payment in db.scalars(select(Payment).where(Payment.status.in_(("approved", "rejected")), Payment.processed_at.is_(None))).all():
        access = None
        if payment.status == "approved":
            if payment.plan_id not in PRO_PLANS:
                continue
            access = activate_pro_plan(db, db.get(Account, payment.account_id), payment.plan_id)
        payment.processed_at = now
        db.commit()
        settled.append((payment, access))
    for payment in db.scalars(select(Payment).where(Payment.status == "waiting_proof", Payment.expires_at <= now)).all():
        payment.status = "expired"
        db.commit()
        settled.append((payment, None))
    return settled


def storage_config() -> dict[str, str] | None:
    url = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not url.startswith("https://") or not key:
        return None
    return {"url": url + "/storage/v1", "key": key, "bucket": os.getenv("PAYMENT_PROOF_BUCKET", "").strip() or "payment-proofs"}


def _storage_headers(config: dict[str, str]) -> dict[str, str]:
    return {"Authorization": "Bearer " + config["key"], "apikey": config["key"]}


async def ensure_proof_bucket() -> None:
    config = storage_config()
    if config is None:
        return
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(config["url"] + "/bucket", headers=_storage_headers(config), json={"id": config["bucket"], "name": config["bucket"], "public": False})
    if response.status_code >= 400 and "exist" not in response.text.lower():
        response.raise_for_status()


async def upload_proof(path: str, data: bytes) -> None:
    config = storage_config()
    if config is None:
        raise RuntimeError("SUPABASE_URL dan SUPABASE_SERVICE_ROLE_KEY belum dikonfigurasi")
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(f"{config['url']}/object/{config['bucket']}/{path}", headers={**_storage_headers(config), "Content-Type": "image/jpeg"}, content=data)
    response.raise_for_status()
