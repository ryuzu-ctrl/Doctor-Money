import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

from backend.database import migrate_auth_schema, resolve_database_url


@pytest.mark.parametrize("scheme", ["postgres://", "postgresql://"])
def test_resolve_database_url_uses_installed_driver_for_postgres(monkeypatch, scheme):
    monkeypatch.setenv(
        "DATABASE_URL",
        f"{scheme}user:password@postgres.railway.internal:5432/railway?sslmode=disable",
    )

    assert resolve_database_url() == (
        "postgresql+psycopg://user:password@postgres.railway.internal:5432/railway"
        "?sslmode=disable"
    )


def test_resolve_database_url_preserves_explicit_driver(monkeypatch):
    configured = "postgresql+psycopg://user:password@localhost:5432/app"
    monkeypatch.setenv("DATABASE_URL", configured)

    assert resolve_database_url() == configured


@pytest.mark.parametrize(
    "scheme",
    ["postgres://", "postgresql://", "postgresql+psycopg://"],
)
def test_resolve_database_url_requires_ssl_for_supabase(monkeypatch, scheme):
    monkeypatch.setenv(
        "DATABASE_URL",
        f"{scheme}postgres.project-ref:password@aws-0-us-east-1.pooler.supabase.com:5432/postgres"
        "?application_name=doctor-money&sslmode=disable",
    )

    resolved = make_url(resolve_database_url())

    assert resolved.drivername == "postgresql+psycopg"
    assert resolved.query["sslmode"] == "require"
    assert resolved.query["application_name"] == "doctor-money"


def test_resolve_database_url_preserves_stronger_supabase_sslmode(monkeypatch):
    configured = (
        "postgresql://user:password@db.project-ref.supabase.co:5432/postgres"
        "?sslmode=verify-full"
    )
    monkeypatch.setenv("DATABASE_URL", configured)

    assert resolve_database_url() == configured


def test_auth_schema_migration_adds_credentials_to_existing_accounts(tmp_path):
    engine = create_engine("sqlite:///" + str(tmp_path / "legacy.db"))
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE accounts (id INTEGER PRIMARY KEY)"))

    migrate_auth_schema(engine)
    columns = {column["name"] for column in inspect(engine).get_columns("accounts")}
    indexes = {index["name"] for index in inspect(engine).get_indexes("accounts")}

    assert {"email", "password_hash"} <= columns
    assert "uq_accounts_email" in indexes
    engine.dispose()
