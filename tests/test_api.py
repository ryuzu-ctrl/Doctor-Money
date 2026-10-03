import os
import tempfile
import uuid

import pytest
from fastapi.testclient import TestClient


_database_path = os.path.join(tempfile.gettempdir(), f"doctor-money-{uuid.uuid4().hex}.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _database_path

from backend.app import app
from backend.database import engine


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def account(client: TestClient) -> tuple[str, dict]:
    response = client.post("/api/accounts", json={"init_data": None})
    assert response.status_code == 200
    body = response.json()
    return body["token"], body["state"]


def test_new_account_is_empty_and_bearer_authenticated(client):
    token, state = account(client)
    assert state["txs"] == []
    assert state["budgets"] == {}
    assert all(wallet["start"] == 0 for wallet in state["wallets"])
    assert client.get("/api/state").status_code == 401
    response = client.get("/api/state", headers={"Authorization": "Bearer " + token})
    assert response.status_code == 200 and response.json()["revision"] == 0


def test_state_save_conflict_and_user_isolation(client):
    token, state = account(client)
    state["name"] = "Dina"
    response = client.put("/api/state", headers={"Authorization": "Bearer " + token}, json={"state": state, "revision": 0})
    assert response.status_code == 200 and response.json()["revision"] == 1
    stale = client.put("/api/state", headers={"Authorization": "Bearer " + token}, json={"state": state, "revision": 0})
    assert stale.status_code == 409
    another_token, _ = account(client)
    other = client.get("/api/state", headers={"Authorization": "Bearer " + another_token})
    assert other.json()["state"]["name"] == "Sobat"


def test_import_preview_skips_seed_transactions_without_writing(client):
    token, _ = account(client)
    headers = {"Authorization": "Bearer " + token}
    legacy = {"name": "Dina", "txs": [{"id": "s1", "date": "2026-10-01", "amt": 20000, "type": "out"}, {"id": "u1", "date": "2026-10-02", "amt": 25000, "type": "out", "cat": "makan", "w": "main"}], "wallets": [{"id": "main", "n": "Utama", "start": 6_000_000}], "budgets": {}, "goals": []}
    preview = client.post("/api/import/preview", headers=headers, json=legacy)
    assert preview.status_code == 200
    assert preview.json()["imported_transactions"] == 1
    assert client.get("/api/state", headers=headers).json()["state"]["txs"] == []
    confirmed = client.post("/api/import/confirm", headers=headers, json={"state": preview.json()["state"]})
    assert confirmed.status_code == 200
    assert client.get("/api/state", headers=headers).json()["state"]["txs"][0]["id"] == "u1"


def teardown_module():
    engine.dispose()
    try:
        os.remove(_database_path)
    except FileNotFoundError:
        pass