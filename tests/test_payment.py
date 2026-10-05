from urllib.parse import unquote

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.database import Base
from backend.pro import pending_or_new_order
from backend.repository import account_for_telegram
from bot.handlers import payment


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv("PRO_PAYMENT_METHOD", "Bank Uji")
    monkeypatch.setenv("PRO_PAYMENT_ACCOUNT", "1234567890")
    monkeypatch.setenv("PRO_PAYMENT_ACCOUNT_NAME", "Admin Uji")
    monkeypatch.delenv("ADMIN_WHATSAPP", raising=False)


def urls(markup):
    return [button.url for row in markup.inline_keyboard for button in row if button.url]


def callbacks(markup):
    return [button.callback_data for row in markup.inline_keyboard for button in row if button.callback_data]


def test_whatsapp_url_strips_plus_and_encodes_text(monkeypatch):
    monkeypatch.setenv("ADMIN_WHATSAPP", "+62 852-1919-6145")
    assert payment.whatsapp_url() == "https://wa.me/6285219196145"
    assert payment.whatsapp_url("Halo Admin").endswith("?text=Halo%20Admin")


def test_unconfigured_payment_only_offers_whatsapp(monkeypatch):
    for name in ("PRO_PAYMENT_METHOD", "PRO_PAYMENT_ACCOUNT", "PRO_PAYMENT_ACCOUNT_NAME", "ADMIN_WHATSAPP"):
        monkeypatch.delenv(name, raising=False)
    text, markup = payment.plan_step()
    assert "belum dibuka" in text
    assert callbacks(markup) == ["menu:home"]
    assert urls(markup)[0].startswith("https://wa.me/6285219196145")


def test_plan_step_offers_dashboard_plans_one_button_per_row(configured):
    text, markup = payment.plan_step()
    assert "Langkah 1 dari 4" in text
    assert callbacks(markup) == ["pay:plan:monthly", "pay:plan:six_months", "pay:plan:annual", "menu:home"]
    assert all(len(row) == 1 for row in markup.inline_keyboard)
    assert markup.inline_keyboard[0][0].text == "1 bulan · Rp 20.000"
    assert markup.inline_keyboard[2][0].text == "1 tahun · Rp 180.000 · hemat Rp 60.000"


def test_transfer_and_proof_steps_carry_account_and_order_id(configured):
    text, markup = payment.transfer_step("monthly", "ABC123")
    assert "Bank Uji" in text and "<code>1234567890</code>" in text and "Admin Uji" in text
    assert "<code>20000</code>" in text and "<code>ABC123</code>" in text
    assert "pay:done:monthly" in callbacks(markup)

    text, markup = payment.proof_step("monthly", "ABC123", 42)
    message = unquote(urls(markup)[0].split("?text=", 1)[1])
    assert "ABC123" in message and "Rp 20.000" in message and "ID Telegram: 42" in message


def test_bot_order_is_reused_until_paid():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, expire_on_commit=False)() as db:
        account = account_for_telegram(db, 4242)
        first = pending_or_new_order(db, account.id, "annual")
        assert first.status == "pending" and first.amount == 180_000
        assert pending_or_new_order(db, account.id, "annual").id == first.id
        assert pending_or_new_order(db, account.id, "monthly").id != first.id
    engine.dispose()
