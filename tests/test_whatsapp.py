import os
import tempfile
import uuid

import pytest
from fastapi.testclient import TestClient


os.environ.setdefault("DATABASE_URL", "sqlite:///" + os.path.join(tempfile.gettempdir(), f"doctor-money-{uuid.uuid4().hex}.db"))

from backend.app import app
from backend.database import Base, engine
from backend.whatsapp import incoming_message, parse_message


SECRET = "test-webhook-secret"


@pytest.mark.parametrize("text, amount", [
    ("keluar 25000 makan", 25_000),
    ("keluar 25.000 makan", 25_000),
    ("keluar 25rb makan", 25_000),
    ("keluar 25k makan", 25_000),
    ("masuk 2jt gaji", 2_000_000),
    ("masuk 1,5jt gaji", 1_500_000),
])
def test_amount_formats(text, amount):
    assert parse_message(text)["amount"] == amount


@pytest.mark.parametrize("text, expected_type", [
    ("keluar 10rb parkir", "out"),
    ("beli buku 80rb", "out"),
    ("bayar listrik 300rb", "out"),
    ("masuk 500rb", "in"),
    ("terima 300k dari budi", "in"),
    ("gaji 9jt", "in"),
    ("bayar gaji karyawan 2jt", "out"),
    ("kopi 25k", "out"),
])
def test_type_keywords(text, expected_type):
    assert parse_message(text)["type"] == expected_type


def test_category_from_hashtag_keyword_and_default():
    assert parse_message("keluar 50rb bensin #transport") == {"kind": "transaction", "type": "out", "amount": 50_000, "category": "transport", "description": "bensin"}
    assert parse_message("keluar 25000 makan siang")["category"] == "makan"
    assert parse_message("keluar 20rb sesuatu")["category"] == "lain"
    assert parse_message("masuk 100rb #bonus")["category"] == "bonus"
    assert parse_message("keluar 20rb #gaji")["category"] == "lain"


def test_commands_and_unknown_messages():
    assert parse_message("Saldo")["kind"] == "balance"
    assert parse_message("hapus")["kind"] == "delete"
    assert parse_message("bantuan")["kind"] == "help"
    assert parse_message("hubungkan 12345678") == {"kind": "link", "code": "12345678"}
    assert parse_message("apa kabar")["kind"] == "unknown"


def payload(number: str, text: str, from_me: bool = False, remote: str | None = None) -> dict:
    return {"event": "messages.received", "data": {"messages": {"key": {"id": uuid.uuid4().hex, "fromMe": from_me, "remoteJid": remote or number + "@s.whatsapp.net", "cleanedSenderPn": number}, "messageBody": text, "message": {"conversation": text}}}}


def test_incoming_message_ignores_own_and_group_messages():
    assert incoming_message(payload("628111", "x")) is None
    assert incoming_message(payload("6281234567890", "keluar 5rb", from_me=True)) is None
    assert incoming_message(payload("6281234567890", "keluar 5rb", remote="1203@g.us")) is None
    assert incoming_message({"event": "session.status", "data": {}}) is None
    assert incoming_message(payload("6281234567890", " keluar 5rb "))[:2] == ("6281234567890", "keluar 5rb")


@pytest.fixture
def wa(monkeypatch):
    monkeypatch.setattr("backend.app.check_rate_limit", lambda *args, **kwargs: True)
    monkeypatch.setenv("WASENDER_API_KEY", "test-api-key")
    monkeypatch.setenv("WASENDER_WEBHOOK_SECRET", SECRET)
    Base.metadata.create_all(bind=engine)  # test_api removes the shared database file when it finishes
    sent: list[tuple[str, str]] = []

    async def capture(number, text):
        sent.append((number, text))

    monkeypatch.setattr("backend.whatsapp.send_text", capture)
    with TestClient(app) as client:
        def send(number, text, secret=SECRET):
            return client.post("/api/whatsapp/webhook", json=payload(number, text), headers={"X-Webhook-Signature": secret})
        yield client, send, sent


def test_webhook_rejects_bad_signature_and_unlinked_numbers(wa):
    client, send, sent = wa
    assert send("6281200000001", "keluar 5rb kopi", secret="wrong").status_code == 401
    assert client.post("/api/whatsapp/webhook", json=payload("6281200000001", "keluar 5rb kopi")).status_code == 401
    assert not sent
    assert send("6281200000001", "keluar 5rb kopi").status_code == 200
    assert "belum terhubung" in sent[-1][1]
    assert send("6281200000001", "hubungkan 00000000").status_code == 200
    assert "tidak valid" in sent[-1][1]


def test_whatsapp_link_record_balance_and_delete_share_dashboard_state(wa):
    client, send, sent = wa
    number = "6281200000002"
    token = client.post("/api/auth/signup", json={"email": f"wa-{uuid.uuid4().hex[:8]}@example.com", "password": "a-long-test-password"}).json()["token"]
    headers = {"Authorization": "Bearer " + token}
    assert client.get("/api/whatsapp/status", headers=headers).json()["connected"] is False
    code = client.post("/api/whatsapp/link-code", headers=headers).json()["code"]

    send(number, "hubungkan " + code)
    assert "terhubung" in sent[-1][1]
    assert client.get("/api/whatsapp/status", headers=headers).json() == {"configured": True, "connected": True, "number": number, "bot_number": ""}
    send("6281200000003", "hubungkan " + code)
    assert "tidak valid" in sent[-1][1]

    send(number, "keluar 25000 makan siang")
    assert sent[-1] == (number, "✅ Tercatat: Pengeluaran Rp 25.000 – makan siang (Makan & Minum)\nDompet: Rekening Utama\nTotal pengeluaran bulan ini: Rp 25.000")
    send(number, "masuk 2jt gaji")
    send(number, "keluar 50rb bensin #transport")
    assert "Total pengeluaran bulan ini: Rp 75.000" in sent[-1][1]

    remote = client.get("/api/state", headers=headers).json()
    assert remote["revision"] == 3
    assert [(tx["type"], tx["amt"], tx["cat"], tx["desc"]) for tx in remote["state"]["txs"]] == [("out", 25_000, "makan", "makan siang"), ("in", 2_000_000, "gaji", "gaji"), ("out", 50_000, "transport", "bensin")]
    assert client.put("/api/state", headers=headers, json={"state": remote["state"], "revision": remote["revision"]}).status_code == 200

    send(number, "saldo")
    assert "Pemasukan: Rp 2.000.000" in sent[-1][1] and "Pengeluaran: Rp 75.000" in sent[-1][1] and "Saldo semua dompet: Rp 1.925.000" in sent[-1][1]
    send(number, "hapus")
    assert "Dihapus: Pengeluaran Rp 50.000" in sent[-1][1]
    assert len(client.get("/api/state", headers=headers).json()["state"]["txs"]) == 2
    send(number, "apa kabar")
    assert "Format belum dikenali" in sent[-1][1]

    assert client.delete("/api/whatsapp/link", headers=headers).status_code == 200
    send(number, "keluar 5rb kopi")
    assert "belum terhubung" in sent[-1][1]
    assert len(client.get("/api/state", headers=headers).json()["state"]["txs"]) == 2
