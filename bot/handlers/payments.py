import logging
import os
from datetime import datetime, timezone
from html import escape
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from backend.database import SessionLocal
from backend.models import Payment
from backend.pro import PRO_PLANS, ensure_pro_access, pro_access_active, pro_payment_config
from backend.repository import account_for_telegram, now_utc, read_bot_state
from bot.keyboards import back_menu
from bot.services import payments
from bot.utils.formatting import rupiah


logger = logging.getLogger("doctor_money.payments")


def _local_time(value: datetime, telegram_id: int) -> str:
    name = read_bot_state(telegram_id).get("preferences", {}).get("timezone", "Asia/Jakarta")
    try:
        zone = ZoneInfo(name)
    except Exception:
        name, zone = "Asia/Jakarta", ZoneInfo("Asia/Jakarta")
    return value.replace(tzinfo=timezone.utc).astimezone(zone).strftime("%d/%m/%Y %H:%M") + f" ({name})"


def _support_line() -> str:
    support_url = os.getenv("SUPPORT_URL", "").strip()
    if support_url.startswith(("https://", "tg://")):
        return f"\n\nButuh bantuan? <a href=\"{escape(support_url, quote=True)}\">Hubungi admin</a>."
    return "\n\nButuh bantuan? Hubungi administrator bot."


def _payment_text(payment: Payment) -> str:
    plan = PRO_PLANS[payment.plan_id]
    config = pro_payment_config()
    lines = [
        "<b>💎 Upgrade Premium</b>",
        f"Paket: Doctor Money PRO {plan['name']}",
        f"Durasi: {plan['months']} bulan",
        f"Total transfer: <code>{rupiah(payment.amount)}</code>",
        "",
    ]
    if payment.status == "pending_review":
        lines.append("⏳ Bukti transfer sudah diterima dan sedang diverifikasi admin. Anda akan mendapat notifikasi di chat ini.")
        return "\n".join(lines) + _support_line()
    lines += [
        "Transfer <b>tepat</b> sesuai nominal di atas (termasuk 3 digit terakhir) agar pembayaran mudah dicocokkan.",
        "",
        f"<b>{escape(config['method'])}</b>: <code>{escape(config['account'])}</code>",
        f"a.n. {escape(config['account_name'])}",
        "",
        f"⏰ Batas pembayaran: {_local_time(payment.expires_at, payment.telegram_id)}",
        "Setelah transfer, tekan tombol di bawah lalu kirim foto bukti transfer.",
    ]
    return "\n".join(lines) + _support_line()


def _payment_keyboard(payment: Payment) -> InlineKeyboardMarkup:
    rows = []
    if payment.status == "waiting_proof":
        qris_url = os.getenv("PRO_PAYMENT_QRIS_URL", "").strip()
        if qris_url.startswith("https://"):
            rows.append([InlineKeyboardButton("🔳 Lihat QRIS", url=qris_url)])
        rows.append([
            InlineKeyboardButton("📤 Kirim Bukti Transfer", callback_data=f"pay:proof:{payment.id}"),
            InlineKeyboardButton("❌ Batal", callback_data=f"pay:cancel:{payment.id}"),
        ])
    rows.append([InlineKeyboardButton("⬅️ Kembali ke Menu", callback_data="menu:home")])
    return InlineKeyboardMarkup(rows)


async def _send(update: Update, text: str, markup: InlineKeyboardMarkup) -> None:
    if update.callback_query:
        await update.callback_query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=markup, disable_web_page_preview=True)
    else:
        await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=markup, disable_web_page_preview=True)


async def show_upgrade(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    with SessionLocal() as db:
        account = account_for_telegram(db, user_id)
        payment = payments.open_payment(db, account.id)
        access = ensure_pro_access(db, account)
    if payment:
        await _send(update, _payment_text(payment), _payment_keyboard(payment))
        return
    if not pro_payment_config()["configured"]:
        await _send(update, "<b>💎 Upgrade Premium</b>\nPembayaran belum tersedia saat ini." + _support_line(), back_menu())
        return
    lines = ["<b>💎 Upgrade Premium</b>"]
    if pro_access_active(access):
        ends_at = access.expires_at if access.status == "active" else access.trial_ends_at
        label = "Pro aktif" if access.status == "active" else "Masa uji coba Pro"
        lines.append(f"{label} sampai {_local_time(ends_at, user_id)}. Paket baru menambah masa aktif.")
    lines.append("\nPilih paket Doctor Money PRO:")
    rows = [[InlineKeyboardButton(f"{plan['name']} · {rupiah(plan['price'])}", callback_data=f"pay:plan:{plan_id}")] for plan_id, plan in PRO_PLANS.items()]
    rows.append([InlineKeyboardButton("⬅️ Kembali ke Menu", callback_data="menu:home")])
    await _send(update, "\n".join(lines), InlineKeyboardMarkup(rows))


async def show_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, key: str) -> bool:
    if key != "upgrade":
        return False
    await show_upgrade(update, context)
    return True


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    query = update.callback_query
    data = query.data or ""
    if not data.startswith("pay:"):
        return False
    _, action, value = (data.split(":", 2) + ["", ""])[:3]
    user_id = update.effective_user.id
    if action == "plan":
        if value not in PRO_PLANS or not pro_payment_config()["configured"]:
            await show_upgrade(update, context)
            return True
        with SessionLocal() as db:
            account = account_for_telegram(db, user_id)
            payment = payments.open_payment(db, account.id)
            # A payment under review blocks a new one; an unpaid one is replaced by the new choice.
            if payment is None or payment.status == "waiting_proof":
                payment = payments.create_payment(db, account, user_id, value)
        await _send(update, _payment_text(payment), _payment_keyboard(payment))
        return True
    if action == "proof":
        await _send(update, "📤 Kirim <b>foto</b> bukti transfer ke chat ini sekarang (sebagai foto, bukan file).", back_menu())
        return True
    if action == "cancel":
        with SessionLocal() as db:
            account = account_for_telegram(db, user_id)
            cancelled = value.isdigit() and payments.cancel_payment(db, account.id, int(value))
        await _send(update, "Pembayaran dibatalkan." if cancelled else "Pembayaran ini sudah tidak dapat dibatalkan.", back_menu())
        return True
    return False


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    with SessionLocal() as db:
        account = account_for_telegram(db, user.id)
        payment = payments.open_payment(db, account.id)
    if payment is None:
        await message.reply_text("Belum ada pembayaran yang menunggu bukti. Pilih 💎 Upgrade Premium di /menu untuk memulai.")
        return
    if payment.status == "pending_review":
        await message.reply_text("Bukti transfer Anda sudah diterima dan sedang diverifikasi admin.")
        return
    photo = message.photo[-1]
    path = f"{user.id}/{payment.id}-{now_utc():%Y%m%d%H%M%S}.jpg"
    try:
        telegram_file = await photo.get_file()
        await payments.upload_proof(path, bytes(await telegram_file.download_as_bytearray()))
    except Exception:
        logger.exception("Upload bukti transfer gagal", extra={"user_id": user.id, "update_id": update.update_id})
        await message.reply_text("Bukti belum dapat diunggah. Silakan kirim ulang fotonya beberapa saat lagi.", reply_markup=back_menu())
        return
    with SessionLocal() as db:
        attached = payments.attach_proof(db, payment.id, path)
    if not attached:
        await message.reply_text("Pembayaran ini sudah tidak menunggu bukti. Buka 💎 Upgrade Premium untuk melihat statusnya.", reply_markup=back_menu())
        return
    await message.reply_text(f"✅ Bukti transfer untuk pembayaran #{payment.id} diterima.\nAdmin akan memverifikasi maksimal 1×24 jam, dan Anda akan mendapat notifikasi di chat ini.", reply_markup=back_menu())
    admin_chat = os.getenv("PRO_ADMIN_CHAT_ID", "").strip()
    if admin_chat.lstrip("-").isdigit():
        caption = f"Bukti transfer baru\nPayment #{payment.id} · {PRO_PLANS[payment.plan_id]['name']} · {rupiah(payment.amount)}\nUser: {user.full_name} (id {user.id})\nFile: {path}\n\nUbah status di tabel payments menjadi approved atau rejected."
        try:
            await context.bot.send_photo(int(admin_chat), photo.file_id, caption=caption)
        except Exception:
            logger.exception("Notifikasi admin gagal", extra={"user_id": user.id})


def _settled_text(payment: Payment, access) -> str:
    plan = PRO_PLANS.get(payment.plan_id, {}).get("name", payment.plan_id)
    if payment.status == "approved":
        return f"🎉 Pembayaran #{payment.id} disetujui.\nDoctor Money PRO {escape(plan)} aktif sampai {_local_time(access.expires_at, payment.telegram_id)}. Terima kasih!"
    if payment.status == "rejected":
        reason = f"\nCatatan admin: {escape(payment.admin_note.strip())}" if (payment.admin_note or "").strip() else ""
        return f"❌ Pembayaran #{payment.id} ({rupiah(payment.amount)}) ditolak.{reason}\nPilih 💎 Upgrade Premium di /menu untuk mencoba lagi." + _support_line()
    return f"⌛ Pembayaran #{payment.id} ({rupiah(payment.amount)}) kedaluwarsa karena bukti transfer tidak diterima dalam 24 jam.\nSudah transfer? Hubungi admin." + _support_line()


async def payments_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        with SessionLocal() as db:
            settled = payments.settle_payments(db)
    except Exception:
        logger.exception("Job pembayaran gagal")
        return
    for payment, access in settled:
        try:
            await context.bot.send_message(payment.telegram_id, _settled_text(payment, access), parse_mode=ParseMode.HTML, disable_web_page_preview=True)
        except Exception:
            logger.exception("Notifikasi pembayaran gagal", extra={"user_id": payment.telegram_id})
