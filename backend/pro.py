import os
import secrets
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import ProOrder
from .repository import now_utc


# Dipakai bersama oleh API dashboard dan alur /bayar di bot agar harga dan order selalu sama.
PRO_PLANS = {
    "monthly": {"name": "1 bulan", "months": 1, "price": 20_000, "monthly_price": 20_000, "savings": 0},
    "six_months": {"name": "6 bulan", "months": 6, "price": 100_000, "monthly_price": 16_667, "savings": 20_000},
    "annual": {"name": "1 tahun", "months": 12, "price": 180_000, "monthly_price": 15_000, "savings": 60_000},
}


def pro_payment_config() -> dict[str, Any]:
    method = os.getenv("PRO_PAYMENT_METHOD", "").strip()
    account = os.getenv("PRO_PAYMENT_ACCOUNT", "").strip()
    account_name = os.getenv("PRO_PAYMENT_ACCOUNT_NAME", "").strip()
    return {"configured": bool(method and account and account_name), "method": method, "account": account, "account_name": account_name}


def pending_or_new_order(db: Session, account_id: int, plan_id: str) -> ProOrder:
    pending = db.scalar(select(ProOrder).where(ProOrder.account_id == account_id, ProOrder.plan_id == plan_id, ProOrder.status == "pending").order_by(ProOrder.created_at.desc()))
    if pending:
        return pending
    order = ProOrder(id=secrets.token_hex(12).upper(), account_id=account_id, plan_id=plan_id, amount=PRO_PLANS[plan_id]["price"], status="pending", created_at=now_utc())
    db.add(order)
    db.commit()
    db.refresh(order)
    return order
