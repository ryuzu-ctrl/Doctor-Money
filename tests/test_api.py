import os
import tempfile
import uuid
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient


_database_path = os.path.join(tempfile.gettempdir(), f"doctor-money-{uuid.uuid4().hex}.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _database_path
os.environ["WEBAPP_URL"] = "https://doctor-money.example.web.app"

from backend.app import app
from backend.database import SessionLocal, engine
from backend.models import Account, PasswordReset, Payment, ProAccess
from backend.pro import FreeLimitReached
from backend.repository import account_for_token, apply_account_mutation, make_pair_code, now_utc


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


def test_email_signup_login_logout_and_existing_account_upgrade(client):
    old_token, state = account(client)
    state["name"] = "Email User"
    saved = client.put(
        "/api/state",
        headers={"Authorization": "Bearer " + old_token},
        json={"state": state, "revision": 0},
    )
    assert saved.status_code == 200

    upgraded = client.post(
        "/api/auth/signup",
        headers={"Authorization": "Bearer " + old_token},
        json={"email": " User@Example.com ", "password": "a-long-test-password"},
    )
    assert upgraded.status_code == 200
    result = upgraded.json()
    assert result["email"] == "user@example.com"
    assert result["state"]["name"] == "Email User"
    assert "password_hash" not in result
    assert client.get("/api/state", headers={"Authorization": "Bearer " + old_token}).status_code == 401

    duplicate = client.post(
        "/api/auth/signup",
        json={"email": "USER@example.com", "password": "another-long-password"},
    )
    assert duplicate.status_code == 409
    short_password = client.post(
        "/api/auth/signup",
        json={"email": "short@example.com", "password": "short"},
    )
    assert short_password.status_code == 422

    wrong_password = client.post(
        "/api/auth/login",
        json={"email": "USER@example.com", "password": "wrong-password"},
    )
    assert wrong_password.status_code == 401
    unknown_email = client.post(
        "/api/auth/login",
        json={"email": "unknown@example.com", "password": "wrong-password"},
    )
    assert unknown_email.status_code == 401
    assert unknown_email.json()["detail"] == wrong_password.json()["detail"]
    login = client.post(
        "/api/auth/login",
        json={"email": " USER@example.com ", "password": "a-long-test-password"},
    )
    assert login.status_code == 200
    login_token = login.json()["token"]
    assert login.json()["email"] == "user@example.com"
    assert client.get("/api/auth/me", headers={"Authorization": "Bearer " + login_token}).json() == {
        "email": "user@example.com",
        "telegram_linked": False,
    }
    assert client.post("/api/auth/logout", headers={"Authorization": "Bearer " + login_token}).status_code == 200
    assert client.get("/api/auth/me", headers={"Authorization": "Bearer " + login_token}).status_code == 401


def test_telegram_link_requires_email_and_works_for_email_account(client):
    anonymous_token, _ = account(client)
    blocked = client.post(
        "/api/link",
        headers={"Authorization": "Bearer " + anonymous_token},
        json={"code": "00000000"},
    )
    assert blocked.status_code == 403

    signed_up = client.post(
        "/api/auth/signup",
        json={"email": "telegram@example.com", "password": "a-long-test-password"},
    )
    assert signed_up.status_code == 200
    token = signed_up.json()["token"]
    with SessionLocal() as db:
        code, _ = make_pair_code(db, 9001001)
    connected = client.post(
        "/api/link",
        headers={"Authorization": "Bearer " + token},
        json={"code": code},
    )
    assert connected.status_code == 200
    assert client.get("/api/link/status", headers={"Authorization": "Bearer " + token}).json()["connected"] is True
    reused = client.post("/api/link", headers={"Authorization": "Bearer " + token}, json={"code": code})
    assert reused.status_code == 409


def test_email_password_reset_preserves_financial_data_and_revokes_sessions(client, monkeypatch):
    monkeypatch.setattr("backend.app.check_rate_limit", lambda *args, **kwargs: True)
    signup = client.post(
        "/api/auth/signup",
        json={"email": "reset@example.com", "password": "initial-long-password"},
    )
    token = signup.json()["token"]
    state = signup.json()["state"]
    state["name"] = "Reset Safe"
    state["txs"] = [{"id": "tx-reset", "date": "2026-10-01", "type": "out", "amt": 1234, "cat": "makan", "desc": "Kopi", "w": "main"}]
    saved = client.put(
        "/api/state",
        headers={"Authorization": "Bearer " + token},
        json={"state": state, "revision": 0},
    )
    assert saved.status_code == 200
    another_session = client.post(
        "/api/auth/login",
        json={"email": "reset@example.com", "password": "initial-long-password"},
    ).json()["token"]

    monkeypatch.setenv("SMTP_HOST", "smtp.example.test")
    monkeypatch.setenv("SMTP_FROM", "support@example.test")
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr("backend.app.send_reset_email", lambda email, code: sent.append((email, code)))
    unknown = client.post(
        "/api/auth/password-reset/request",
        json={"email": "missing@example.com", "method": "email"},
    )
    request = client.post(
        "/api/auth/password-reset/request",
        json={"email": " RESET@example.com ", "method": "email"},
    )
    assert unknown.status_code == request.status_code == 200
    assert unknown.json() == request.json()
    assert sent and sent[0][0] == "reset@example.com"
    code = sent[0][1]
    with SessionLocal() as db:
        account_id = db.query(Account.id).filter(Account.email == "reset@example.com").scalar()
        challenge = db.query(PasswordReset).filter(PasswordReset.account_id == account_id).one()
        assert challenge.code_hash != code

    invalid_code = "00000000" if code != "00000000" else "00000001"
    wrong_code = client.post(
        "/api/auth/password-reset/confirm",
        json={"email": "reset@example.com", "method": "email", "code": invalid_code, "new_password": "replacement-long-password"},
    )
    assert wrong_code.status_code == 400
    changed = client.post(
        "/api/auth/password-reset/confirm",
        json={"email": "reset@example.com", "method": "email", "code": code, "new_password": "replacement-long-password"},
    )
    assert changed.status_code == 200
    assert client.get("/api/auth/me", headers={"Authorization": "Bearer " + token}).status_code == 401
    assert client.get("/api/auth/me", headers={"Authorization": "Bearer " + another_session}).status_code == 401

    old_password = client.post(
        "/api/auth/login",
        json={"email": "reset@example.com", "password": "initial-long-password"},
    )
    new_password = client.post(
        "/api/auth/login",
        json={"email": "reset@example.com", "password": "replacement-long-password"},
    )
    assert old_password.status_code == 401
    assert new_password.status_code == 200
    state_after_reset = client.get(
        "/api/state",
        headers={"Authorization": "Bearer " + new_password.json()["token"]},
    ).json()["state"]
    assert state_after_reset["name"] == "Reset Safe"
    assert state_after_reset["txs"] == state["txs"]
    reused = client.post(
        "/api/auth/password-reset/confirm",
        json={"email": "reset@example.com", "method": "email", "code": code, "new_password": "another-replacement-password"},
    )
    assert reused.status_code == 400


def test_telegram_password_reset_requires_linked_account_and_limits_code_attempts(client, monkeypatch):
    monkeypatch.setattr("backend.app.check_rate_limit", lambda *args, **kwargs: True)
    monkeypatch.setenv("BOT_TOKEN", "test-bot-token")
    sent: list[tuple[int, str]] = []

    async def capture_telegram(telegram_id: int, code: str) -> None:
        sent.append((telegram_id, code))

    monkeypatch.setattr("backend.app.send_reset_telegram", capture_telegram)
    signup = client.post(
        "/api/auth/signup",
        json={"email": "telegram-reset@example.com", "password": "initial-long-password"},
    )
    token = signup.json()["token"]
    with SessionLocal() as db:
        code, _ = make_pair_code(db, 9001002)
    linked = client.post(
        "/api/link",
        headers={"Authorization": "Bearer " + token},
        json={"code": code},
    )
    assert linked.status_code == 200

    requested = client.post(
        "/api/auth/password-reset/request",
        json={"email": "telegram-reset@example.com", "method": "telegram"},
    )
    assert requested.status_code == 200
    assert sent and sent[0][0] == 9001002
    reset_code = sent[0][1]
    for _ in range(5):
        invalid = client.post(
            "/api/auth/password-reset/confirm",
            json={"email": "telegram-reset@example.com", "method": "telegram", "code": "00000000", "new_password": "replacement-long-password"},
        )
        assert invalid.status_code == 400
    exhausted = client.post(
        "/api/auth/password-reset/confirm",
        json={"email": "telegram-reset@example.com", "method": "telegram", "code": reset_code, "new_password": "replacement-long-password"},
    )
    assert exhausted.status_code == 400
    assert client.post(
        "/api/auth/login",
        json={"email": "telegram-reset@example.com", "password": "initial-long-password"},
    ).status_code == 200


def test_telegram_password_reset_updates_password_when_code_is_valid(client, monkeypatch):
    monkeypatch.setattr("backend.app.check_rate_limit", lambda *args, **kwargs: True)
    monkeypatch.setenv("BOT_TOKEN", "test-bot-token")
    sent: list[tuple[int, str]] = []

    async def capture_telegram(telegram_id: int, code: str) -> None:
        sent.append((telegram_id, code))

    monkeypatch.setattr("backend.app.send_reset_telegram", capture_telegram)
    signup = client.post(
        "/api/auth/signup",
        json={"email": "telegram-success@example.com", "password": "initial-long-password"},
    )
    token = signup.json()["token"]
    with SessionLocal() as db:
        code, _ = make_pair_code(db, 9001003)
    assert client.post(
        "/api/link",
        headers={"Authorization": "Bearer " + token},
        json={"code": code},
    ).status_code == 200
    assert client.post(
        "/api/auth/password-reset/request",
        json={"email": "telegram-success@example.com", "method": "telegram"},
    ).status_code == 200
    assert sent and sent[0][0] == 9001003
    changed = client.post(
        "/api/auth/password-reset/confirm",
        json={"email": "telegram-success@example.com", "method": "telegram", "code": sent[0][1], "new_password": "replacement-long-password"},
    )
    assert changed.status_code == 200
    assert client.get("/api/auth/me", headers={"Authorization": "Bearer " + token}).status_code == 401
    assert client.post(
        "/api/auth/login",
        json={"email": "telegram-success@example.com", "password": "replacement-long-password"},
    ).status_code == 200


def test_password_reset_delivery_failure_invalidates_code_and_returns_error(client, monkeypatch):
    monkeypatch.setattr("backend.app.check_rate_limit", lambda *args, **kwargs: True)
    monkeypatch.setenv("SMTP_HOST", "smtp.example.test")
    monkeypatch.setenv("SMTP_FROM", "support@example.test")
    signup = client.post(
        "/api/auth/signup",
        json={"email": "smtp-failure@example.com", "password": "initial-long-password"},
    )
    assert signup.status_code == 200

    def fail_email(email: str, code: str) -> None:
        raise OSError("SMTP unavailable")

    monkeypatch.setattr("backend.app.send_reset_email", fail_email)
    response = client.post(
        "/api/auth/password-reset/request",
        json={"email": "smtp-failure@example.com", "method": "email"},
    )
    assert response.status_code == 503
    with SessionLocal() as db:
        account_id = db.query(Account.id).filter(Account.email == "smtp-failure@example.com").scalar()
        challenge = db.query(PasswordReset).filter(PasswordReset.account_id == account_id).one()
        assert challenge.consumed_at is not None


def test_market_assets_endpoint_returns_yahoo_quotes_without_account_auth(client, monkeypatch):
    async def market_quotes():
        return {
            "stocks": [{"symbol": "BBCA.JK", "name": "Bank Central Asia", "price": 10500, "change_pct": 5, "currency": "IDR", "unit": "per saham"}],
            "commodities": [{"symbol": "GC=F", "name": "Emas futures", "price": 2000, "change_pct": -1, "currency": "USD", "unit": "per troy ounce"}],
            "stocks_monitored": 30,
            "stocks_as_of": 1790932499,
            "commodities_as_of": 1790932499,
        }

    monkeypatch.setattr("backend.app.market.yahoo_market_data", market_quotes)
    response = client.get("/api/market/assets")
    assert response.status_code == 200
    assert response.json()["stocks"][0]["symbol"] == "BBCA.JK"
    assert response.json()["stocks_monitored"] == 30
    assert response.json()["stocks_as_of"] == 1790932499
    assert response.json()["commodities"][0]["currency"] == "USD"

    async def unavailable():
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr("backend.app.market.yahoo_market_data", unavailable)
    failed = client.get("/api/market/assets")
    assert failed.status_code == 503
    assert "Yahoo Finance" in failed.json()["detail"]


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


def end_trial(token: str) -> None:
    with SessionLocal() as db:
        access = db.get(ProAccess, account_for_token(db, token).id)
        access.status = "expired"
        access.trial_started_at = now_utc() - timedelta(days=62)
        access.trial_ends_at = now_utc() - timedelta(days=31)
        db.commit()


def month_state(state: dict, count: int, month: str = "2026-03") -> dict:
    wallet = state["wallets"][0]["id"]
    return {**state, "txs": [{"id": f"t{index}", "date": f"{month}-01", "desc": "Kopi", "cat": "makan", "amt": 1000, "w": wallet, "type": "out"} for index in range(count)]}


def test_trial_lasts_one_month_and_old_seven_day_trials_are_extended(client):
    token, _ = account(client)
    headers = {"Authorization": "Bearer " + token}
    status = client.get("/api/pro/status", headers=headers).json()
    started = datetime.fromisoformat(status["trial_started_at"].replace("Z", "+00:00"))
    ended = datetime.fromisoformat(status["trial_ends_at"].replace("Z", "+00:00"))
    assert 28 <= (ended - started).days <= 31
    assert status["tier"] == "pro" and status["free_monthly_transactions"] == 50

    with SessionLocal() as db:
        access = db.get(ProAccess, account_for_token(db, token).id)
        access.status = "expired"
        access.trial_started_at = now_utc() - timedelta(days=10)
        access.trial_ends_at = now_utc() - timedelta(days=3)
        db.commit()
    revived = client.get("/api/pro/status", headers=headers).json()
    assert revived["status"] == "trial" and revived["active"] is True


def test_free_account_keeps_dashboard_data_with_monthly_transaction_limit(client):
    token, state = account(client)
    headers = {"Authorization": "Bearer " + token}
    client.get("/api/pro/status", headers=headers)
    end_trial(token)

    status = client.get("/api/pro/status", headers=headers).json()
    assert status["status"] == "expired" and status["active"] is False and status["tier"] == "free"
    assert client.get("/api/state", headers=headers).status_code == 200
    saved = client.put("/api/state", headers=headers, json={"state": month_state(state, 50), "revision": 0})
    assert saved.status_code == 200
    over = client.put("/api/state", headers=headers, json={"state": month_state(state, 51), "revision": 1})
    assert over.status_code == 402 and "50 transaksi" in over.json()["detail"]
    other_month = month_state(state, 50)
    other_month["txs"].append({**other_month["txs"][0], "id": "april", "date": "2026-04-01"})
    assert client.put("/api/state", headers=headers, json={"state": other_month, "revision": 1}).status_code == 200
    order = client.post("/api/pro/orders", headers=headers, json={"plan_id": "annual"})
    assert order.status_code == 200 and order.json()["amount"] == 180_000


def test_free_account_can_still_edit_months_recorded_during_pro(client):
    token, state = account(client)
    headers = {"Authorization": "Bearer " + token}
    full = month_state(state, 80)
    assert client.put("/api/state", headers=headers, json={"state": full, "revision": 0}).status_code == 200
    end_trial(token)

    full["txs"][0]["desc"] = "Teh"
    assert client.put("/api/state", headers=headers, json={"state": full, "revision": 1}).status_code == 200
    full["txs"].pop()
    assert client.put("/api/state", headers=headers, json={"state": full, "revision": 2}).status_code == 200
    full["txs"].extend([{**full["txs"][0], "id": "n1"}, {**full["txs"][0], "id": "n2"}])
    assert client.put("/api/state", headers=headers, json={"state": full, "revision": 3}).status_code == 402


def test_free_limit_applies_to_bot_and_whatsapp_mutations(client):
    token, state = account(client)
    headers = {"Authorization": "Bearer " + token}
    month = now_utc().strftime("%Y-%m")
    assert client.put("/api/state", headers=headers, json={"state": month_state(state, 50, month), "revision": 0}).status_code == 200
    with SessionLocal() as db:
        account_id = account_for_token(db, token).id

    def record(current):
        current["txs"].append({**current["txs"][0], "id": "extra"})

    apply_account_mutation(account_id, record)
    end_trial(token)
    with pytest.raises(FreeLimitReached):
        apply_account_mutation(account_id, record)
    assert len(client.get("/api/state", headers=headers).json()["state"]["txs"]) == 51
    apply_account_mutation(account_id, lambda current: current["txs"].pop())


def seed_month(client, headers, state, month):
    wallet = state["wallets"][0]["id"]
    state = {**state, "txs": [
        {"id": "g1", "date": month + "-01", "desc": "Gaji", "cat": "gaji", "amt": 8_000_000, "w": wallet, "type": "in"},
        {"id": "m1", "date": month + "-02", "desc": "Makan", "cat": "makan", "amt": 1_500_000, "w": wallet, "type": "out"},
    ]}
    assert client.put("/api/state", headers=headers, json={"state": state, "revision": 0}).status_code == 200
    return state


AI_RESULT = {"headline": "Arus kasmu sehat.", "summary": "Ringkasan.", "strengths": ["Menabung."], "risks": [], "insights": [], "prescription": ["Sisihkan dana darurat."], "outlook": ""}


def test_ai_analysis_uses_account_data_caches_and_enforces_free_limit(client, monkeypatch):
    token, state = account(client)
    headers = {"Authorization": "Bearer " + token}
    seen = []
    monkeypatch.setattr("backend.ai.generate", lambda facts: seen.append(facts) or dict(AI_RESULT))
    monkeypatch.setenv("AI_FREE_MONTHLY_LIMIT", "2")
    assert client.post("/api/ai/analyze", json={"mode": "full"}).status_code == 401

    empty = client.post("/api/ai/analyze", headers=headers, json={"mode": "full"})
    assert empty.status_code == 200 and empty.json()["status"] == "no_data" and not seen

    month = now_utc().strftime("%Y-%m")
    state = seed_month(client, headers, state, month)
    pro = client.post("/api/ai/analyze", headers=headers, json={"mode": "spending", "context": "tx"}).json()
    assert pro["status"] == "ok" and pro["tier"] == "pro" and pro["analysis"]["headline"] == "Arus kasmu sehat."
    assert pro["facts"]["income"] == 8_000_000 and pro["facts"]["expenses"] == 1_500_000
    assert seen[-1]["depth"] == "full" and seen[-1]["top_expense_categories"][0]["amount"] == 1_500_000
    assert "txs" not in seen[-1] and "previous_months" in seen[-1]

    end_trial(token)
    assert client.post("/api/ai/analyze", headers=headers, json={"mode": "spending"}).status_code == 402
    first = client.post("/api/ai/analyze", headers=headers, json={"mode": "full"}).json()
    assert first["cached"] is False and first["tier"] == "free" and seen[-1]["depth"] == "basic" and "budgets" not in seen[-1]
    calls = len(seen)
    again = client.post("/api/ai/analyze", headers=headers, json={"mode": "full"}).json()
    assert again["cached"] is True and len(seen) == calls and again["usage"] == first["usage"]

    state["txs"].append({**state["txs"][1], "id": "m2"})
    assert client.put("/api/state", headers=headers, json={"state": state, "revision": 1}).status_code == 200
    status = client.get("/api/ai/status", headers=headers).json()
    assert status["tier"] == "free" and status["limit"] == 2 and status["remaining"] == 0
    blocked = client.post("/api/ai/analyze", headers=headers, json={"mode": "full"})
    assert blocked.status_code == 402 and "sudah habis" in blocked.json()["detail"] and len(seen) == calls


def test_ai_analysis_reports_unavailable_without_spending_quota(client, monkeypatch):
    token, state = account(client)
    headers = {"Authorization": "Bearer " + token}
    seed_month(client, headers, state, now_utc().strftime("%Y-%m"))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert client.get("/api/ai/status", headers=headers).json()["configured"] is False
    failed = client.post("/api/ai/analyze", headers=headers, json={"mode": "full"})
    assert failed.status_code == 503 and "belum diaktifkan" in failed.json()["detail"]
    assert client.get("/api/ai/status", headers=headers).json()["used"] == 0


def test_admin_lists_accounts_and_grants_or_revokes_pro(client, monkeypatch):
    monkeypatch.setenv("PRO_ADMIN_SECRET", "test-pro-admin-secret")
    admin = {"Authorization": "Bearer test-pro-admin-secret"}
    email = f"admin-{uuid.uuid4().hex[:8]}@Example.com"
    token = client.post("/api/auth/signup", json={"email": email, "password": "a-long-test-password"}).json()["token"]
    headers = {"Authorization": "Bearer " + token}
    client.get("/api/pro/status", headers=headers)
    end_trial(token)

    assert client.get("/api/admin/accounts").status_code == 401
    assert client.get("/api/admin/accounts", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/api/admin/accounts", headers=admin, params={"q": "100%_nobody"}).json()["accounts"] == []
    found = client.get("/api/admin/accounts", headers=admin, params={"q": email[:14].upper()}).json()
    assert found["total"] >= 1 and [row["email"] for row in found["accounts"]] == [email.lower()]
    row = found["accounts"][0]
    assert row["tier"] == "free" and row["ends_at"] is None

    assert client.post(f"/api/admin/accounts/{row['id']}/pro", json={"plan_id": "annual"}).status_code == 401
    granted = client.post(f"/api/admin/accounts/{row['id']}/pro", headers=admin, json={"plan_id": "annual"}).json()
    assert granted["tier"] == "pro" and granted["plan"] == "1 tahun" and granted["ends_at"]
    assert client.get("/api/pro/status", headers=headers).json()["active"] is True
    assert client.post("/api/admin/accounts/999999/pro", headers=admin, json={"plan_id": "monthly"}).status_code == 404

    revoked = client.delete(f"/api/admin/accounts/{row['id']}/pro", headers=admin).json()
    assert revoked["tier"] == "free"
    status = client.get("/api/pro/status", headers=headers).json()
    assert status["active"] is False and status["tier"] == "free"


def test_admin_approves_bot_payment_once(client, monkeypatch):
    monkeypatch.setenv("PRO_ADMIN_SECRET", "test-pro-admin-secret")
    admin = {"Authorization": "Bearer test-pro-admin-secret"}
    token, _ = account(client)
    with SessionLocal() as db:
        account_id = account_for_token(db, token).id
        payment = Payment(account_id=account_id, telegram_id=777001, plan_id="monthly", amount=20_123, status="pending_review", created_at=now_utc(), expires_at=now_utc() + timedelta(hours=24), proof_at=now_utc())
        db.add(payment)
        db.commit()
        payment_id = payment.id
    listed = client.get("/api/admin/payments", headers=admin).json()["payments"]
    assert [(row["id"], row["amount"]) for row in listed if row["id"] == payment_id] == [(payment_id, 20_123)]
    assert client.post(f"/api/admin/payments/{payment_id}/approve", headers=admin).json() == {"status": "approved"}
    assert client.post(f"/api/admin/payments/{payment_id}/reject", headers=admin).status_code == 409
    assert all(row["id"] != payment_id for row in client.get("/api/admin/payments", headers=admin).json()["payments"])


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
    assert 'id="paysheet"' in dashboard.text
    assert 'href="/"' in dashboard.text


def teardown_module():
    engine.dispose()
    try:
        os.remove(_database_path)
    except FileNotFoundError:
        pass

def test_profile_update_and_password_change(client, monkeypatch):
    monkeypatch.setattr("backend.app.check_rate_limit", lambda *args, **kwargs: True)
    signup = client.post("/api/auth/signup", json={"email": "profile@example.com", "password": "initial-long-password"})
    token = signup.json()["token"]
    headers = {"Authorization": "Bearer " + token}
    other = client.post("/api/auth/signup", json={"email": "taken@example.com", "password": "another-long-password"})
    assert other.status_code == 200

    profile = client.get("/api/auth/profile", headers=headers).json()
    assert profile["email"] == "profile@example.com" and profile["avatar"] is None
    assert profile["has_password"] is True and profile["name"] == "Sobat" and profile["created_at"]
    assert "password_hash" not in profile
    assert client.get("/api/auth/profile").status_code == 401

    avatar = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
    updated = client.put("/api/auth/profile", headers=headers, json={"name": "  Budi  Santoso ", "avatar": avatar})
    assert updated.status_code == 200
    assert updated.json()["name"] == "Budi Santoso" and updated.json()["avatar"] == avatar
    assert updated.json()["revision"] == profile["revision"] + 1
    assert client.get("/api/state", headers=headers).json()["state"]["name"] == "Budi Santoso"
    for bad in ("data:image/svg+xml;base64,PHN2Zz48L3N2Zz4=", "data:image/png;base64,PHN2Zz48L3N2Zz4=", "https://example.com/a.png"):
        assert client.put("/api/auth/profile", headers=headers, json={"avatar": bad}).status_code == 422
    assert client.put("/api/auth/profile", headers=headers, json={"name": "   "}).status_code == 422
    assert client.put("/api/auth/profile", headers=headers, json={"avatar": ""}).json()["avatar"] is None

    assert client.put("/api/auth/profile", headers=headers, json={"email": "new@example.com"}).status_code == 403
    assert client.put("/api/auth/profile", headers=headers, json={"email": "new@example.com", "current_password": "wrong-password"}).status_code == 403
    assert client.put("/api/auth/profile", headers=headers, json={"email": "TAKEN@example.com", "current_password": "initial-long-password"}).status_code == 409
    moved = client.put("/api/auth/profile", headers=headers, json={"email": " New@Example.com ", "current_password": "initial-long-password"})
    assert moved.status_code == 200 and moved.json()["email"] == "new@example.com"

    other_session = client.post("/api/auth/login", json={"email": "new@example.com", "password": "initial-long-password"}).json()["token"]
    wrong = client.post("/api/auth/password", headers=headers, json={"current_password": "wrong-password", "new_password": "replacement-long-password"})
    assert wrong.status_code == 403
    assert client.post("/api/auth/password", headers=headers, json={"current_password": "initial-long-password", "new_password": "short"}).status_code == 422
    assert client.post("/api/auth/password", headers=headers, json={"current_password": "initial-long-password", "new_password": "initial-long-password"}).status_code == 422
    changed = client.post("/api/auth/password", headers=headers, json={"current_password": "initial-long-password", "new_password": "replacement-long-password"})
    assert changed.status_code == 200
    new_headers = {"Authorization": "Bearer " + changed.json()["token"]}
    assert client.get("/api/auth/profile", headers=headers).status_code == 401
    assert client.get("/api/auth/profile", headers={"Authorization": "Bearer " + other_session}).status_code == 401
    assert client.get("/api/auth/profile", headers=new_headers).status_code == 200
    assert client.post("/api/auth/login", json={"email": "new@example.com", "password": "initial-long-password"}).status_code == 401
    assert client.post("/api/auth/login", json={"email": "new@example.com", "password": "replacement-long-password"}).status_code == 200

    # Repeated wrong guesses of the current password are throttled per account.
    statuses = [client.post("/api/auth/password", headers=new_headers, json={"current_password": "wrong-password", "new_password": "yet-another-long-password"}).status_code for _ in range(3)]
    assert statuses == [403, 403, 429]
    assert client.post("/api/auth/password", headers=new_headers, json={"current_password": "replacement-long-password", "new_password": "yet-another-long-password"}).status_code == 429

    anonymous_token, _ = account(client)
    anonymous = {"Authorization": "Bearer " + anonymous_token}
    assert client.get("/api/auth/profile", headers=anonymous).json()["has_password"] is False
    assert client.post("/api/auth/password", headers=anonymous, json={"current_password": "whatever-password", "new_password": "replacement-long-password"}).status_code == 409
    assert client.put("/api/auth/profile", headers=anonymous, json={"email": "anon@example.com"}).status_code == 409
