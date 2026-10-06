import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import pytest

from cc.security import InitDataError, validate_init_data
from bot.keyboards import main_menu


def make_init_data(token: str, user_id: int = 12345, auth_date: int | None = None) -> str:
    values = {"auth_date": str(auth_date or int(time.time())), "query_id": "query-test", "user": json.dumps({"id": user_id, "first_name": "Dina"}, separators=(",", ":"))}
    check_string = "\n".join(f"{key}={value}" for key, value in sorted(values.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    values["hash"] = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode(values)


def test_valid_init_data_returns_telegram_user():
    result = validate_init_data(make_init_data("bot-secret"), "bot-secret")
    assert result["id"] == 12345
    assert result["first_name"] == "Dina"


def test_invalid_or_stale_init_data_is_rejected():
    with pytest.raises(InitDataError):
        validate_init_data(make_init_data("one-token"), "another-token")
    with pytest.raises(InitDataError):
        validate_init_data(make_init_data("bot-secret", auth_date=int(time.time()) - 100_000), "bot-secret")


def test_main_menu_layout_and_callback_data():
    keyboard = main_menu("https://example.test/app")
    rows = keyboard.inline_keyboard
    assert len(rows) == 12
    assert len(rows[0]) == 1 and rows[0][0].web_app.url == "https://example.test/app"
    assert all(len(row) == 2 for row in rows[1:])
    callbacks = [button.callback_data for row in rows[1:] for button in row]
    assert callbacks[0] == "menu:top_crypto"
    assert callbacks[-1] == "menu:help"
    assert all(value.startswith("menu:") for value in callbacks)