import os
from urllib.parse import quote_plus

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker


load_dotenv()


def resolve_database_url() -> str:
    configured = os.getenv("DATABASE_URL")
    if configured:
        return configured

    pguser = os.getenv("PGUSER") or os.getenv("POSTGRES_USER")
    pgpassword = os.getenv("POSTGRES_PASSWORD")
    pghost = os.getenv("PGHOST") or os.getenv("RAILWAY_PRIVATE_DOMAIN")
    pgdatabase = os.getenv("PGDATABASE") or os.getenv("POSTGRES_DB")
    pgport = os.getenv("PGPORT", "5432")

    if pguser and pgpassword and pghost and pgdatabase:
        if "supabase.co" in pghost or "supabase" in pghost:
            sslmode = "require"
        else:
            sslmode = "disable"
        return (
            f"postgresql+psycopg://{quote_plus(pguser)}:{quote_plus(pgpassword)}@{pghost}:{pgport}/{pgdatabase}"
            f"?sslmode={sslmode}"
        )

    return "sqlite:///./doctor_money.db"


DATABASE_URL = resolve_database_url()
_connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=_connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass