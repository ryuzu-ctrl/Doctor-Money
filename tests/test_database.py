from sqlalchemy import create_engine, inspect, text

from backend.database import migrate_auth_schema


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
