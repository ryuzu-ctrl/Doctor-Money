import os
import logging

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker


load_dotenv()
logger = logging.getLogger(__name__)


def _normalize_url(url: str) -> str:
    # Railway/Supabase memberi postgres:// atau postgresql://; driver yang terpasang adalah psycopg 3.
    url = url.strip().strip("\"'")
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


DATABASE_URL = _normalize_url(os.getenv("DATABASE_URL", "") or "sqlite:///./doctor_money.db")

# Hanya validasi strict jika sudah clear bahwa itu BUKAN variable reference
# (variable reference harus sudah di-expand oleh Railway saat runtime)
_is_reference = "${{" in DATABASE_URL or "DATABASE_URL=" in DATABASE_URL
_is_valid_format = DATABASE_URL.startswith(("sqlite", "postgresql+psycopg://"))

if not _is_reference and not _is_valid_format:
    _scheme = DATABASE_URL.split("://", 1)[0][:40]
    raise RuntimeError(
        "DATABASE_URL tidak valid (diawali " + repr(_scheme) + ", panjang " + str(len(DATABASE_URL)) + " karakter). "
        "Di Railway isi dengan ${{<nama service Postgres>.DATABASE_URL}} dan pastikan nama service-nya sama persis."
    )

if _is_reference:
    logger.warning(f"DATABASE_URL berisi variable reference. Railway seharusnya expand ini saat runtime. Nilai: {DATABASE_URL[:80]}")

_connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=_connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass

