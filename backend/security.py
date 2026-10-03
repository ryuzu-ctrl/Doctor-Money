import hashlib
import hmac
import json
import os
import time
from urllib.parse import parse_qsl


class InitDataError(ValueError):
    pass


def validate_init_data(init_data: str, bot_token: str | None = None, max_age: int = 86_400) -> dict:
    token = bot_token or os.getenv("BOT_TOKEN", "")
    if not token:
        raise InitDataError("BOT_TOKEN belum dikonfigurasi")
    if len(init_data) > 16_384:
        raise InitDataError("initData terlalu panjang")
    values = dict(parse_qsl(init_data, keep_blank_values=True, strict_parsing=True))
    supplied_hash = values.pop("hash", "")
    if not supplied_hash or len(supplied_hash) != 64:
        raise InitDataError("Hash initData tidak valid")
    check_string = "\n".join(f"{key}={value}" for key, value in sorted(values.items()))
    secret_key = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    expected_hash = hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected_hash, supplied_hash):
        raise InitDataError("Tanda tangan initData tidak valid")
    try:
        auth_date = int(values["auth_date"])
        user = json.loads(values["user"])
        user_id = int(user["id"])
    except (KeyError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise InitDataError("Identitas initData tidak valid") from exc
    now = int(time.time())
    if auth_date > now + 60 or now - auth_date > max_age:
        raise InitDataError("initData sudah kedaluwarsa")
    if user_id <= 0:
        raise InitDataError("ID Telegram tidak valid")
    return user