import os
from urllib.parse import quote_plus

from dotenv import load_dotenv
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, sessionmaker


load_dotenv()


def resolve_database_url() -> str:
    configured = os.getenv("DATABASE_URL")
    if configured:
        if configured.startswith("postgres://"):
            configured = "postgresql+psycopg://" + configured.removeprefix("postgres://")
        elif configured.startswith("postgresql://"):
            configured = "postgresql+psycopg://" + configured.removeprefix("postgresql://")
        database_url = make_url(configured)
        if database_url.host and "supabase" in database_url.host.lower():
            query = dict(database_url.query)
            if query.get("sslmode") not in {"require", "verify-ca", "verify-full"}:
                query["sslmode"] = "require"
                return database_url.set(query=query).render_as_string(hide_password=False)
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


def migrate_auth_schema(target_engine=engine) -> None:
    inspector = inspect(target_engine)
    if "accounts" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("accounts")}
    with target_engine.begin() as connection:
        if "email" not in columns:
            connection.execute(text("ALTER TABLE accounts ADD COLUMN email VARCHAR(320)"))
        if "password_hash" not in columns:
            connection.execute(text("ALTER TABLE accounts ADD COLUMN password_hash VARCHAR(255)"))
        if "whatsapp_number" not in columns:
            connection.execute(text("ALTER TABLE accounts ADD COLUMN whatsapp_number VARCHAR(20)"))
        if "avatar" not in columns:
            connection.execute(text("ALTER TABLE accounts ADD COLUMN avatar TEXT"))
        connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_accounts_email ON accounts (email)"))
        connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_accounts_whatsapp_number ON accounts (whatsapp_number)"))
        if target_engine.dialect.name == "postgresql":
            for table in ("accounts", "api_tokens", "pair_codes", "password_resets", "whatsapp_link_codes"):
                connection.execute(text(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY'))