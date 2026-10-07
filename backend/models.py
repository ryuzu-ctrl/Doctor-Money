from datetime import datetime

from sqlalchemy import BigInteger, Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, deferred, mapped_column, relationship

from .database import Base


class Account(Base):
    __tablename__ = "accounts"
    __table_args__ = (Index("uq_accounts_email", "email", unique=True), Index("uq_accounts_whatsapp_number", "whatsapp_number", unique=True))

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_id: Mapped[int | None] = mapped_column(BigInteger, unique=True, index=True)
    email: Mapped[str | None] = mapped_column(String(320))
    password_hash: Mapped[str | None] = mapped_column(String(255))
    whatsapp_number: Mapped[str | None] = mapped_column(String(20))
    # Profile photo as a small data URL; deferred so ordinary requests do not load it.
    avatar: Mapped[str | None] = deferred(mapped_column(Text))
    dashboard_linked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    state_json: Mapped[str] = mapped_column(Text, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    tokens: Mapped[list["ApiToken"]] = relationship(cascade="all, delete-orphan")


class ApiToken(Base):
    __tablename__ = "api_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class PairCode(Base):
    __tablename__ = "pair_codes"
    __table_args__ = (UniqueConstraint("code_hash"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime)


class WhatsAppLinkCode(Base):
    __tablename__ = "whatsapp_link_codes"
    __table_args__ = (UniqueConstraint("code_hash"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime)


class PasswordReset(Base):
    __tablename__ = "password_resets"
    __table_args__ = (Index("ix_password_resets_account_method_created", "account_id", "method", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False)
    method: Mapped[str] = mapped_column(String(8), nullable=False)
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    telegram_id: Mapped[int | None] = mapped_column(BigInteger)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime)


class ProAccess(Base):
    __tablename__ = "pro_access"

    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), primary_key=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    trial_started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    trial_ends_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    plan_id: Mapped[str | None] = mapped_column(String(24))
    starts_at: Mapped[datetime | None] = mapped_column(DateTime)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class ProOrder(Base):
    __tablename__ = "pro_orders"
    __table_args__ = (UniqueConstraint("id"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
    plan_id: Mapped[str] = mapped_column(String(24), nullable=False)
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime)


PAYMENT_STATUSES = ("waiting_proof", "pending_review", "approved", "rejected", "expired", "cancelled")


class Payment(Base):
    __tablename__ = "payments"
    # The admin edits status by hand in the Supabase Table Editor; the check rejects typos.
    __table_args__ = (
        CheckConstraint("status IN ('" + "', '".join(PAYMENT_STATUSES) + "')", name="ck_payments_status"),
        Index("ix_payments_status_processed", "status", "processed_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    plan_id: Mapped[str] = mapped_column(String(24), nullable=False)
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    proof_path: Mapped[str | None] = mapped_column(String(255))
    admin_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    proof_at: Mapped[datetime | None] = mapped_column(DateTime)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime)