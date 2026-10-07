import logging
import os
import re
import secrets
import uuid
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Account, WhatsAppLinkCode
from .repository import apply_account_mutation, hash_secret, now_utc
from bot.services.finance import CATEGORIES, IN_ORDER, OUT_ORDER, dashboard_score, format_rupiah, parse_transaction


logger = logging.getLogger("doctor_money.whatsapp")
SEND_URL = "https://www.wasenderapi.com/api/send-message"
MESSAGE_EVENTS = {"messages.received", "messages.upsert"}
TYPE_RE = re.compile(r"\b(?:(keluar|beli|bayar)|(masuk|terima|gaji))\b", re.I)
HASHTAG_RE = re.compile(r"#(\w+)")
LINK_RE = re.compile(r"^(?:hubungkan|link)\s+(\d{8})$", re.I)
CATEGORY_ALIASES = {"makanan": "makan", "transportasi": "transport", "kendaraan": "transport", "lainnya": "lain", "tagihan": "tagihan", "sehat": "kesehatan", "sekolah": "pendidikan"}
HELP_TEXT = (
    "*Doctor Money – catat lewat WhatsApp*\n"
    "\n"
    "Catat transaksi:\n"
    "• keluar 25000 makan siang\n"
    "• masuk 2jt gaji\n"
    "• keluar 50rb bensin #transport\n"
    "\n"
    "Kata kunci: keluar/beli/bayar = pengeluaran, masuk/terima/gaji = pemasukan.\n"
    "Nominal: 25000, 25.000, 25rb, 25k, 2jt, 1,5jt.\n"
    "Kategori: tambahkan #makan, #transport, #belanja, #tagihan, #hiburan, #kesehatan, #pendidikan, #gaji, #freelance, atau #bonus. Tanpa hashtag, kategori ditebak dari keterangan.\n"
    "\n"
    "Perintah lain:\n"
    "• saldo – ringkasan bulan ini\n"
    "• hapus – hapus transaksi terakhir yang dicatat lewat WhatsApp\n"
    "• bantuan – tampilkan pesan ini"
)
UNKNOWN_TEXT = "⚠️ Format belum dikenali karena nominalnya tidak terbaca.\nContoh: *keluar 25rb makan siang* atau *masuk 2jt gaji*.\nKetik *bantuan* untuk daftar perintah."
UNLINKED_TEXT = "Nomor ini belum terhubung ke akun Doctor Money. Buka dashboard → Dompet → Hubungkan WhatsApp, buat kode, lalu kirim *hubungkan KODE* ke nomor ini."


def configured() -> bool:
    return bool(os.getenv("WASENDER_API_KEY", "").strip() and os.getenv("WASENDER_WEBHOOK_SECRET", "").strip())


def bot_number() -> str:
    return re.sub(r"\D", "", os.getenv("WHATSAPP_BOT_NUMBER", ""))


def incoming_message(payload: dict[str, Any]) -> tuple[str, str, str] | None:
    """Return (sender number, text, message id) for a private text message, else None."""
    if payload.get("event") not in MESSAGE_EVENTS:
        return None
    data = payload.get("data")
    message = data.get("messages") if isinstance(data, dict) else None
    if isinstance(message, list):
        message = message[0] if message else None
    if not isinstance(message, dict):
        return None
    key = message.get("key") if isinstance(message.get("key"), dict) else {}
    remote = str(key.get("remoteJid") or "")
    if key.get("fromMe") or not remote or remote.endswith(("@g.us", "@newsletter", "@broadcast")):
        return None
    number = re.sub(r"\D", "", str(key.get("cleanedSenderPn") or str(key.get("senderPn") or "").partition("@")[0]))
    if not number and remote.endswith("@s.whatsapp.net"):
        number = re.sub(r"\D", "", remote.partition("@")[0])
    content = message.get("message") if isinstance(message.get("message"), dict) else {}
    extended = content.get("extendedTextMessage") if isinstance(content.get("extendedTextMessage"), dict) else {}
    text = message.get("messageBody") or content.get("conversation") or extended.get("text")
    if not 8 <= len(number) <= 15 or not isinstance(text, str) or not text.strip():
        return None
    return number, text.strip()[:300], str(key.get("id") or "")


def _hashtag_category(tags: list[str]) -> str | None:
    for tag in tags:
        key = CATEGORY_ALIASES.get(tag.lower(), tag.lower())
        if key in CATEGORIES:
            return key
    return None


def parse_message(text: str) -> dict[str, Any]:
    text = re.sub(r"\s+", " ", text).strip()
    command = text.lower().strip(" .!?/")
    if command in {"saldo", "ringkasan"}:
        return {"kind": "balance"}
    if command in {"hapus", "batal"}:
        return {"kind": "delete"}
    if command in {"bantuan", "help", "menu", "mulai", "start", "halo", "hai"}:
        return {"kind": "help"}
    link = LINK_RE.match(text)
    if link:
        return {"kind": "link", "code": link.group(1)}

    tags = HASHTAG_RE.findall(text)
    plain = re.sub(r"\s+", " ", HASHTAG_RE.sub(" ", text)).strip()
    keyword = TYPE_RE.search(plain)
    parsed = parse_transaction(re.sub(r"^(?:keluar|masuk)\b\s*", "", plain, flags=re.I))
    if not parsed or parsed["amount"] > 10**15:
        return {"kind": "unknown"}
    tagged = _hashtag_category(tags)
    if keyword:
        transaction_type = "out" if keyword.group(1) else "in"
    elif tagged:
        transaction_type = CATEGORIES[tagged]["type"]
    else:
        transaction_type = parsed["type"]
    if tagged in {"lain", "lain_in"}:
        tagged = "lain_in" if transaction_type == "in" else "lain"
    if tagged and CATEGORIES[tagged]["type"] == transaction_type:
        category = tagged
    else:
        hints = plain + " " + " ".join(tags)
        order = IN_ORDER if transaction_type == "in" else OUT_ORDER
        category = next((key for key in order if re.search(CATEGORIES[key]["terms"], hints, re.I)), "lain_in" if transaction_type == "in" else "lain")
    description = parsed["description"]
    if description in {item["name"] for item in CATEGORIES.values()}:
        description = CATEGORIES[category]["name"]
    return {"kind": "transaction", "type": transaction_type, "amount": parsed["amount"], "category": category, "description": description}


def _today(state: dict[str, Any]) -> str:
    try:
        zone = ZoneInfo(state.get("preferences", {}).get("timezone", "Asia/Jakarta"))
    except Exception:
        zone = ZoneInfo("Asia/Jakarta")
    return datetime.now(zone).date().isoformat()


def _record(state: dict[str, Any], command: dict[str, Any]) -> dict[str, Any]:
    wallet = state.get("wallets", [{}])[0]
    transaction = {"id": "w" + uuid.uuid4().hex[:16], "date": _today(state), "desc": command["description"], "cat": command["category"], "amt": command["amount"], "w": wallet.get("id", "main"), "type": command["type"], "src": "whatsapp"}
    state.setdefault("txs", []).append(transaction)
    return transaction


def _remove_last(state: dict[str, Any]) -> dict[str, Any] | None:
    transactions = state.get("txs", [])
    for index in range(len(transactions) - 1, -1, -1):
        if transactions[index].get("src") == "whatsapp":
            return transactions.pop(index)
    return None


def _line(transaction: dict[str, Any]) -> str:
    label = "Pemasukan" if transaction["type"] == "in" else "Pengeluaran"
    category = CATEGORIES.get(transaction.get("cat"), CATEGORIES["lain"])["name"]
    return f"{label} {format_rupiah(transaction['amt'])} – {transaction.get('desc') or category} ({category})"


def reply_for(account_id: int, command: dict[str, Any]) -> str:
    kind = command["kind"]
    if kind == "help":
        return HELP_TEXT
    if kind in {"unknown", "link"}:
        return UNKNOWN_TEXT
    if kind == "delete":
        _, removed = apply_account_mutation(account_id, _remove_last)
        return "🗑️ Dihapus: " + _line(removed) if removed else "Belum ada transaksi dari WhatsApp yang bisa dihapus."
    if kind == "transaction":
        state, transaction = apply_account_mutation(account_id, lambda current: _record(current, command))
    else:
        state, transaction = apply_account_mutation(account_id, lambda current: None)
    month = _today(state)[:7]
    summary = dashboard_score(state, month)
    if transaction:
        wallet = next((item.get("n", "Dompet") for item in state.get("wallets", []) if item.get("id") == transaction["w"]), "Dompet")
        total = ("Total pemasukan bulan ini: " + format_rupiah(summary["income"])) if transaction["type"] == "in" else ("Total pengeluaran bulan ini: " + format_rupiah(summary["expenses"]))
        return f"✅ Tercatat: {_line(transaction)}\nDompet: {wallet}\n{total}"
    balance = sum(int(wallet.get("start", 0)) for wallet in state.get("wallets", []))
    balance += sum(int(item.get("amt", 0)) * (1 if item.get("type") == "in" else -1) for item in state.get("txs", []))
    net = summary["net"]
    return (
        f"*Ringkasan {month}*\n"
        f"Pemasukan: {format_rupiah(summary['income'])}\n"
        f"Pengeluaran: {format_rupiah(summary['expenses'])}\n"
        f"Selisih: {'-' if net < 0 else ''}{format_rupiah(net)}\n"
        f"Saldo semua dompet: {'-' if balance < 0 else ''}{format_rupiah(balance)}"
    )


def make_link_code(db: Session, account: Account) -> tuple[str, datetime]:
    now = now_utc()
    db.query(WhatsAppLinkCode).filter(WhatsAppLinkCode.account_id == account.id, WhatsAppLinkCode.consumed_at.is_(None)).update({"consumed_at": now}, synchronize_session=False)
    code = f"{secrets.randbelow(100_000_000):08d}"
    expiry = now + timedelta(minutes=10)
    db.add(WhatsAppLinkCode(code_hash=hash_secret(code), account_id=account.id, expires_at=expiry))
    db.commit()
    return code, expiry


def link_number(db: Session, number: str, code: str) -> str:
    now = now_utc()
    link = db.scalar(select(WhatsAppLinkCode).where(WhatsAppLinkCode.code_hash == hash_secret(code), WhatsAppLinkCode.consumed_at.is_(None), WhatsAppLinkCode.expires_at > now))
    account = db.get(Account, link.account_id) if link else None
    if account is None:
        return "Kode tidak valid atau kedaluwarsa. Buat kode baru di dashboard → Dompet → Hubungkan WhatsApp."
    previous = db.scalar(select(Account).where(Account.whatsapp_number == number, Account.id != account.id))
    if previous:
        previous.whatsapp_number = None
        db.flush()
    account.whatsapp_number = number
    link.consumed_at = now
    db.commit()
    return f"✅ Nomor ini terhubung ke akun {account.email}. Transaksi yang Anda kirim di sini akan muncul di dashboard dan bot Telegram.\n\nCoba: *keluar 25rb makan siang*. Ketik *bantuan* untuk daftar perintah."


async def send_text(number: str, text: str) -> None:
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
            response = await client.post(SEND_URL, headers={"Authorization": "Bearer " + os.getenv("WASENDER_API_KEY", "").strip()}, json={"to": "+" + number, "text": text})
            response.raise_for_status()
    except httpx.HTTPError:
        logger.warning("Balasan WhatsApp gagal dikirim", exc_info=True)
