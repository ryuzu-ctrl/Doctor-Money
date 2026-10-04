import logging
import os
import time
from datetime import datetime

from sqlalchemy.exc import OperationalError
from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base, engine


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_id: Mapped[int | None] = mapped_column(BigInteger, unique=True, index=True)
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

def _wait_for_database() -> None:
    # Jaringan private Railway (*.railway.internal) bisa belum siap beberapa detik setelah container start.
    attempts = int(os.getenv("DB_CONNECT_ATTEMPTS", "30"))
    for attempt in range(1, attempts + 1):
        try:
            with engine.connect():
                return
        except OperationalError as exc:
            if attempt == attempts:
                raise
            logging.getLogger("doctor_money").warning("Database belum terjangkau (percobaan %s/%s): %s", attempt, attempts, str(exc).splitlines()[0])
            time.sleep(2)


def init_db() -> None:
    """Buat tabel; di PostgreSQL perlebar kolom telegram_id lama ke BIGINT (ID Telegram > 2^31)."""
    _wait_for_database()
    Base.metadata.create_all(bind=engine)
    if engine.dialect.name == "postgresql":
        with engine.begin() as conn:
            for table in ("accounts", "pair_codes"):
                data_type = conn.exec_driver_sql(
                    "SELECT data_type FROM information_schema.columns WHERE table_name = %s AND column_name = 'telegram_id'",
                    (table,),
                ).scalar()
                if data_type == "integer":
                    conn.exec_driver_sql(f"ALTER TABLE {table} ALTER COLUMN telegram_id TYPE BIGINT")
