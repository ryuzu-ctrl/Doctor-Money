import calendar
import os
from collections import Counter
from datetime import datetime
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .models import Account, ProAccess
from .repository import now_utc


PRO_PLANS = {
    "monthly": {"name": "1 bulan", "months": 1, "price": 20_000, "monthly_price": 20_000, "savings": 0},
    "six_months": {"name": "6 bulan", "months": 6, "price": 100_000, "monthly_price": 16_667, "savings": 20_000},
    "annual": {"name": "1 tahun", "months": 12, "price": 180_000, "monthly_price": 15_000, "savings": 60_000},
}
TRIAL_MONTHS = 1
FREE_MONTHLY_TRANSACTIONS = 50
FREE_LIMIT_MESSAGE = f"Paket Gratis dibatasi {FREE_MONTHLY_TRANSACTIONS} transaksi per bulan. Upgrade ke Doctor Money Pro untuk mencatat tanpa batas."


class FreeLimitReached(Exception):
    pass


def add_months(value: datetime, months: int) -> datetime:
    month_index = value.year * 12 + value.month - 1 + months
    year, month_zero = divmod(month_index, 12)
    month = month_zero + 1
    return value.replace(year=year, month=month, day=min(value.day, calendar.monthrange(year, month)[1]))


def ensure_pro_access(db: Session, account: Account) -> ProAccess:
    access = db.get(ProAccess, account.id)
    if access is None:
        now = now_utc()
        access = ProAccess(account_id=account.id, status="trial", trial_started_at=now, trial_ends_at=add_months(now, TRIAL_MONTHS), updated_at=now)
        db.add(access)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            access = db.get(ProAccess, account.id)
            if access is None:
                raise
        else:
            db.refresh(access)
    now = now_utc()
    # Accounts that never paid and began under the old 7-day trial get the full trial period.
    trial_end = add_months(access.trial_started_at, TRIAL_MONTHS)
    if access.plan_id is None and access.status in {"trial", "expired"} and access.trial_ends_at < trial_end:
        access.trial_ends_at = trial_end
        access.status = "trial"
        access.updated_at = now
        db.commit()
    ends_at = access.expires_at if access.status == "active" else access.trial_ends_at if access.status == "trial" else None
    if ends_at is not None and ends_at <= now:
        access.status = "expired"
        access.updated_at = now
        db.commit()
    return access


def pro_access_active(access: ProAccess) -> bool:
    now = now_utc()
    ends_at = access.expires_at if access.status == "active" else access.trial_ends_at if access.status == "trial" else None
    return ends_at is not None and ends_at > now


def pro_payment_config() -> dict[str, Any]:
    method = os.getenv("PRO_PAYMENT_METHOD", "").strip()
    account = os.getenv("PRO_PAYMENT_ACCOUNT", "").strip()
    account_name = os.getenv("PRO_PAYMENT_ACCOUNT_NAME", "").strip()
    return {"configured": bool(method and account and account_name), "method": method, "account": account, "account_name": account_name}


def account_has_pro(account_id: int) -> bool:
    from .database import SessionLocal

    with SessionLocal() as db:
        account = db.get(Account, account_id)
        return account is not None and pro_access_active(ensure_pro_access(db, account))


def month_counts(state: dict[str, Any]) -> Counter:
    return Counter(str(tx.get("date", ""))[:7] for tx in state.get("txs", []) if isinstance(tx, dict))


def over_free_limit(before: Counter, after: dict[str, Any]) -> bool:
    """True when a change grows any month's transactions past the free limit; existing data stays editable."""
    return any(count > FREE_MONTHLY_TRANSACTIONS and count > before[month] for month, count in month_counts(after).items())


def activate_pro_plan(db: Session, account: Account, plan_id: str) -> ProAccess:
    """Extend the account's Pro access by one plan period. The caller commits."""
    access = ensure_pro_access(db, account)
    now = now_utc()
    current_end = access.expires_at if access.status == "active" else access.trial_ends_at if access.status == "trial" else None
    starts_at = current_end if current_end and current_end > now else now
    access.status = "active"
    access.plan_id = plan_id
    access.starts_at = starts_at
    access.expires_at = add_months(starts_at, PRO_PLANS[plan_id]["months"])
    access.updated_at = now
    return access
