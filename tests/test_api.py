import os
import tempfile
import uuid

import pytest
from fastapi.testclient import TestClient


_database_path = os.path.join(tempfile.gettempdir(), f"doctor-money-{uuid.uuid4().hex}.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _database_path
os.environ["WEBAPP_URL"] = "https://doctor-money.example.web.app"

from backend.app import app
from backend.database import SessionLocal, engine
from backend.models import ProAccess
from backend.repository import account_for_token, now_utc


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


def test_firebase_hosting_origin_is_allowed_for_api_requests(client):
    response = client.options(
        "/api/accounts",
        headers={
            "Origin": "https://doctor-money.example.web.app",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "https://doctor-money.example.web.app"
    assert "authorization" in response.headers["access-control-allow-headers"].lower()

    blocked = client.options(
        "/api/accounts",
        headers={
            "Origin": "https://untrusted.example",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert blocked.status_code == 400


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


def test_pro_trial_manual_order_and_admin_activation(client, monkeypatch):
    token, _ = account(client)
    headers = {"Authorization": "Bearer " + token}
    monkeypatch.setenv("PRO_PAYMENT_METHOD", "BCA")
    monkeypatch.setenv("PRO_PAYMENT_ACCOUNT", "1234567890")
    monkeypatch.setenv("PRO_PAYMENT_ACCOUNT_NAME", "Doctor Money")
    monkeypatch.setenv("PRO_ADMIN_SECRET", "test-pro-admin-secret")

    status = client.get("/api/pro/status", headers=headers)
    assert status.status_code == 200
    assert status.json()["status"] == "trial" and status.json()["active"] is True
    assert [(plan["id"], plan["price"]) for plan in status.json()["plans"]] == [
        ("monthly", 20_000), ("six_months", 100_000), ("annual", 180_000)
    ]

    order = client.post("/api/pro/orders", headers=headers, json={"plan_id": "six_months"})
    assert order.status_code == 200
    order_body = order.json()
    assert order_body["amount"] == 100_000
    assert order_body["payment"]["account"] == "1234567890"
    duplicate = client.post("/api/pro/orders", headers=headers, json={"plan_id": "six_months"})
    assert duplicate.status_code == 200 and duplicate.json()["order_id"] == order_body["order_id"]
    admin_headers = {"Authorization": "Bearer test-pro-admin-secret"}
    pending = client.get("/api/admin/pro/orders", headers=admin_headers)
    assert [item["order_id"] for item in pending.json()["orders"]] == [order_body["order_id"]]
    denied = client.post(f"/api/admin/pro/orders/{order_body['order_id']}/activate", headers={"Authorization": "Bearer wrong"})
    assert denied.status_code == 401

    activated = client.post(f"/api/admin/pro/orders/{order_body['order_id']}/activate", headers=admin_headers)
    assert activated.status_code == 200 and activated.json()["status"] == "paid"
    assert client.get("/api/state", headers=headers).status_code == 200
    assert client.get("/api/pro/status", headers=headers).json()["plan"]["id"] == "six_months"


def test_expired_pro_blocks_dashboard_data_but_allows_new_order(client):
    token, _ = account(client)
    headers = {"Authorization": "Bearer " + token}
    client.get("/api/pro/status", headers=headers)
    with SessionLocal() as db:
        account_row = account_for_token(db, token)
        access = db.get(ProAccess, account_row.id)
        access.status = "expired"
        access.trial_ends_at = now_utc()
        db.commit()

    assert client.get("/api/state", headers=headers).status_code == 402
    order = client.post("/api/pro/orders", headers=headers, json={"plan_id": "annual"})
    assert order.status_code == 200 and order.json()["amount"] == 180_000


def test_root_and_dashboard_routes_are_available(client):
    landing = client.get("/")
    assert landing.status_code == 200
    assert "t.me" in landing.text.lower() or "telegram" in landing.text.lower()
    assert 'href="/dashboard"' in landing.text
    assert 'id="pro"' in landing.text
    assert 'href="/dashboard?plan=six_months"' in landing.text
    assert "Rp 180.000" in landing.text

    dashboard = client.get("/dashboard")
    assert dashboard.status_code == 200
    assert "Doctor Money" in dashboard.text
    assert "/api/pro/status" in dashboard.text
    assert "Pilih paket" in dashboard.text
    assert 'id="addForm"' in dashboard.text
    assert 'href="/"' in dashboard.text


def teardown_module():
    engine.dispose()
    try:
        os.remove(_database_path)
    except FileNotFoundError:
        pass