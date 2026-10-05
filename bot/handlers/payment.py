import os
import re
from html import escape
from urllib.parse import quote

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from backend.database import SessionLocal
from backend.pro import PRO_PLANS, pending_or_new_order, pro_payment_config
from backend.repository import account_for_telegram
from bot.utils.formatting import rupiah


DEFAULT_ADMIN_WHATSAPP = "6285219196145"


def whatsapp_url(text: str = "") -> str:
    # wa.me hanya menerima angka dengan kode negara, tanpa "+" atau spasi.
    number = re.sub(r"\D", "", os.getenv("ADMIN_WHATSAPP", "")) or DEFAULT_ADMIN_WHATSAPP
    return f"https://wa.me/{number}" + (f"?text={quote(text)}" if text else "")


def _support_button(label: str = "💬 WhatsApp Admin", text: str = "Halo Admin Doctor Money, saya butuh bantuan.") -> InlineKeyboardButton:
    return InlineKeyboardButton(label, url=whatsapp_url(text))


def _back_row() -> list[InlineKeyboardButton]:
    return [InlineKeyboardButton("⬅️ Kembali ke Menu", callback_data="menu:home")]


def support_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[_support_button()], _back_row()])


# Pesan dibuat pendek dan setiap tombol satu baris penuh supaya nyaman di layar ponsel.
def plan_step() -> tuple[str, InlineKeyboardMarkup]:
    if not pro_payment_config()["configured"]:
        text = ("<b>💎 Doctor Money Pro</b>\n"
                "Pembayaran lewat bot belum dibuka. Hubungi admin lewat WhatsApp untuk info paket dan cara bayar.")
        return text, support_keyboard()
    text = ("<b>💎 Doctor Money Pro</b>\n"
            "Bayar dengan transfer ke rekening admin, lalu konfirmasi lewat WhatsApp.\n\n"
            "1. Pilih paket\n"
            "2. Transfer sesuai nominal\n"
            "3. Kirim bukti ke WhatsApp admin\n"
            "4. Admin mengaktifkan Pro Anda\n\n"
            "<b>Langkah 1 dari 4 · Pilih paket</b>")
    rows = []
    for plan_id, plan in PRO_PLANS.items():
        label = f"{plan['name']} · {rupiah(plan['price'])}" + (f" · hemat {rupiah(plan['savings'])}" if plan["savings"] else "")
        rows.append([InlineKeyboardButton(label, callback_data=f"pay:plan:{plan_id}")])
    rows += [[_support_button()], _back_row()]
    return text, InlineKeyboardMarkup(rows)


def transfer_step(plan_id: str, order_id: str) -> tuple[str, InlineKeyboardMarkup]:
    plan, payment = PRO_PLANS[plan_id], pro_payment_config()
    text = ("<b>Langkah 2 dari 4 · Transfer</b>\n"
            f"Paket Pro {escape(plan['name'])}\n\n"
            f"Nominal\n<code>{plan['price']}</code> ({rupiah(plan['price'])})\n\n"
            f"{escape(payment['method'])}\n<code>{escape(payment['account'])}</code>\n"
            f"a.n. <b>{escape(payment['account_name'])}</b>\n\n"
            f"Kode pesanan\n<code>{escape(order_id)}</code>\n\n"
            "Ketuk angka untuk menyalin. Cek nama pemilik rekening sebelum mengirim, lalu simpan bukti transfernya.")
    rows = [
        [InlineKeyboardButton("✅ Saya sudah transfer", callback_data=f"pay:done:{plan_id}")],
        [InlineKeyboardButton("↩️ Ganti paket", callback_data="pay:home")],
        [_support_button()], _back_row(),
    ]
    return text, InlineKeyboardMarkup(rows)


def proof_step(plan_id: str, order_id: str, telegram_id: int) -> tuple[str, InlineKeyboardMarkup]:
    plan = PRO_PLANS[plan_id]
    message = ("Halo Admin Doctor Money, saya sudah transfer untuk Pro.\n"
               f"Kode pesanan: {order_id}\nPaket: {plan['name']}\nNominal: {rupiah(plan['price'])}\nID Telegram: {telegram_id}\n"
               "Bukti transfer saya lampirkan.")
    text = ("<b>Langkah 3 dari 4 · Kirim bukti</b>\n"
            "Ketuk tombol di bawah. Pesan untuk admin sudah disiapkan; lampirkan foto bukti transfer, lalu kirim.\n\n"
            "<b>Langkah 4 dari 4 · Aktivasi</b>\n"
            "Admin mencocokkan bukti dengan dana masuk, lalu mengaktifkan Pro dan mengabari Anda lewat WhatsApp.\n\n"
            f"Kode pesanan\n<code>{escape(order_id)}</code>")
    rows = [
        [_support_button("📤 Kirim bukti ke WhatsApp Admin", message)],
        [InlineKeyboardButton("↩️ Lihat rekening lagi", callback_data=f"pay:plan:{plan_id}")],
        _back_row(),
    ]
    return text, InlineKeyboardMarkup(rows)


async def show_payment(update: Update, context: ContextTypes.DEFAULT_TYPE, edit: bool = False) -> None:
    text, markup = plan_step()
    if edit and update.callback_query:
        await update.callback_query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=markup)
    else:
        await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=markup)


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    query = update.callback_query
    data = query.data or ""
    if not data.startswith("pay:"):
        return False
    _, action, *rest = data.split(":")
    plan_id = rest[0] if rest else ""
    if action not in {"plan", "done"} or plan_id not in PRO_PLANS or not pro_payment_config()["configured"]:
        await show_payment(update, context, edit=True)
        return True
    user_id = update.effective_user.id
    # Order yang sama dengan dashboard: admin mengaktifkannya lewat /api/admin/pro/orders/{id}/activate.
    with SessionLocal() as db:
        order_id = pending_or_new_order(db, account_for_telegram(db, user_id).id, plan_id).id
    text, markup = transfer_step(plan_id, order_id) if action == "plan" else proof_step(plan_id, order_id, user_id)
    await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=markup)
    return True
