import asyncio
import hashlib
import json
import logging
import os
import re
import time
from datetime import datetime
from html import escape
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, MenuButtonWebApp, Update, WebAppInfo
from telegram.constants import ParseMode
from telegram.ext import AIORateLimiter, Application, ApplicationBuilder, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from backend.database import Base, SessionLocal, engine
from backend.models import Account
from backend.pro import FreeLimitReached
from backend.repository import account_for_telegram, make_pair_code, read_bot_state
from bot.handlers import finance, markets, payments
from bot.keyboards import back_menu, main_menu
from bot.services.finance import dashboard_score, empty_state
from bot.services.payments import ensure_proof_bucket
from bot.utils.formatting import rupiah


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        result = {"time": datetime.utcnow().isoformat(timespec="seconds") + "Z", "level": record.levelname, "logger": record.name, "message": record.getMessage()}
        for key in ("user_id", "update_id"):
            if hasattr(record, key):
                result[key] = getattr(record, key)
        if record.exc_info:
            result["exception"] = self.formatException(record.exc_info)
        return json.dumps(result, ensure_ascii=False)


handler = logging.StreamHandler()
handler.setFormatter(JsonFormatter())
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), handlers=[handler], force=True)
# httpx logs full request URLs at INFO, which include the bot token.
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("doctor_money")
LAST_REQUEST: dict[int, float] = {}


def _webapp_url() -> str:
    value = os.getenv("WEBAPP_URL", "").strip()
    if not value.startswith("https://"):
        raise RuntimeError("WEBAPP_URL wajib berisi URL HTTPS publik")
    return value


def _menu_text() -> str:
    return (
        "🩺 DOCTOR MONEY ASSISTANT\n"
        "Asisten keuangan pribadi Anda\n"
        "\n"
        "Kelola keuangan lebih mudah dalam satu tempat.\n"
        "Catat transaksi, pantau kondisi finansial, analisis kebiasaan, hingga lihat gambaran masa depan keuangan Anda.\n"
        "\n"
        "⭐ INGIN FITUR LEBIH LENGKAP?\n"
        "Gunakan Doctor Money PRO untuk membuka pengalaman finansial yang lebih powerful.\n"
        "\n"
        "📋 Pilih menu yang ingin Anda gunakan:"
    )


async def _linked(update: Update) -> bool:
    with SessionLocal() as db:
        account = account_for_telegram(db, update.effective_user.id)
        return bool(account.dashboard_linked)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(_menu_text(), reply_markup=main_menu(_webapp_url()))
    if not await _linked(update):
        await update.effective_message.reply_text("Data keuangan bot ini belum terhubung dengan dashboard. Gunakan menu Hubungkan Akun untuk menyinkronkan.")


async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(_menu_text(), reply_markup=main_menu(_webapp_url()))


async def connect_account(update: Update, context: ContextTypes.DEFAULT_TYPE, edit: bool = False) -> None:
    with SessionLocal() as db:
        account = account_for_telegram(db, update.effective_user.id)
        if account.dashboard_linked:
            text = "Akun ini sudah terhubung ke dashboard. Untuk memutuskan, gunakan halaman Dompet di dashboard."
            markup = back_menu()
        else:
            code, expires_at = make_pair_code(db, update.effective_user.id)
            text = f"<b>Kode pengaitan akun</b>\n<code>{code}</code>\n\nMasuk atau buat akun email di dashboard, lalu masukkan kode ini pada Dompet → Hubungkan Telegram. Kode berlaku sampai {expires_at.strftime('%H:%M')} UTC dan hanya dapat digunakan sekali."
            markup = back_menu()
    if edit and update.callback_query:
        await update.callback_query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=markup)
    else:
        await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=markup)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = ("<b>Bantuan Doctor Money</b>\n"
            "/menu menu utama · /catat transaksi · /riwayat transaksi · /saldo ringkasan dompet\n"
            "/wallet kelola dompet · /transfer antar dompet · /budget batas kategori\n"
            "/laporan ringkasan bulanan · /grafik pengeluaran · /pengingat tagihan\n"
            "/simulasi setoran bunga tahun · /hubungkan akun dashboard · /zona ubah zona waktu\n"
            "/topcrypto · /crypto · /indeks · /saham · /kurs · /watchlist · /alert · /portofolio · /emas · /feargreed\n"
            "/hapusdata hapus data setelah dua konfirmasi\n\n"
            "Contoh transaksi: <code>kopi 25.000</code>, <code>beli buku 1,5jt</code>, <code>bensin 50rb</code>, <code>gaji 9jt</code>.\n"
            "Contoh simulasi: <code>1000000 8 10</code>.\nDukungan: hubungi administrator bot.")
    support_url = os.getenv("SUPPORT_URL", "").strip()
    if support_url.startswith(("https://", "tg://")):
        text += f"\n\n<a href=\"{escape(support_url, quote=True)}\">Hubungi dukungan</a>"
    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=back_menu())


async def timezone_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = read_bot_state(update.effective_user.id)
    if not context.args:
        current = state.get("preferences", {}).get("timezone", "Asia/Jakarta")
        await update.effective_message.reply_text(f"Zona waktu saat ini: {escape(current)}.\nContoh: /zona Asia/Makassar", parse_mode=ParseMode.HTML)
        return
    name = context.args[0][:64]
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        await update.effective_message.reply_text("Zona waktu tidak dikenal. Gunakan format IANA, misalnya Asia/Jakarta.")
        return
    from backend.repository import apply_bot_mutation
    apply_bot_mutation(update.effective_user.id, lambda current: current.setdefault("preferences", {}).update(timezone=name))
    await update.effective_message.reply_text(f"Zona waktu diubah ke {escape(name)}.", parse_mode=ParseMode.HTML)


async def delete_data(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    markup = InlineKeyboardMarkup([[InlineKeyboardButton("Lanjutkan", callback_data="delete-data:arm"), InlineKeyboardButton("Batal", callback_data="menu:home")]])
    await update.effective_message.reply_text("<b>Hapus semua data keuangan?</b>\nTahap 1 dari 2. Dompet, transaksi, budget, pengingat, alert, dan portofolio akan dihapus.", parse_mode=ParseMode.HTML, reply_markup=markup)


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    try:
        await query.answer()
        data = (query.data or "")[:128]
        callback_user_id = update.effective_user.id
        callback_now = time.monotonic()
        if callback_now - LAST_REQUEST.get(callback_user_id, 0) < 0.2:
            return
        LAST_REQUEST[callback_user_id] = callback_now
        if data.startswith("menu:"):
            key = data.split(":", 1)[1]
            if key == "home":
                context.user_data.clear()
                await query.edit_message_text(_menu_text(), reply_markup=main_menu(_webapp_url()))
                return
            if key == "help":
                await query.edit_message_text("<b>Bantuan Doctor Money</b>\n/catat, /riwayat, /saldo, /wallet, /transfer, /budget, /laporan, /grafik, /pengingat, /simulasi, /hubungkan, /zona, /hapusdata.\n\nContoh: <code>kopi 25.000</code> atau <code>gaji 9jt</code>.", parse_mode=ParseMode.HTML, reply_markup=back_menu())
                return
            if key == "connect":
                await connect_account(update, context, edit=True)
                return
            if await payments.show_menu(update, context, key):
                return
            if await finance.show_menu(update, context, key):
                return
            if await markets.show_menu(update, context, key, edit=True):
                return
            await query.edit_message_text("Menu belum tersedia.", reply_markup=back_menu())
            return
        if data.startswith("delete-data:"):
            action = data.split(":", 1)[1]
            if action == "arm":
                context.user_data["delete_data_armed"] = time.monotonic() + 60
                await query.edit_message_text("Tahap 2 dari 2. Konfirmasi permanen untuk menghapus seluruh data.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Hapus permanen", callback_data="delete-data:confirm"), InlineKeyboardButton("Batal", callback_data="menu:home")]]))
            elif action == "confirm" and context.user_data.pop("delete_data_armed", 0) > time.monotonic():
                from backend.repository import apply_bot_mutation
                apply_bot_mutation(update.effective_user.id, lambda state: state.clear() or state.update(empty_state()))
                context.user_data.clear()
                await query.edit_message_text("Semua data akun bot telah dihapus.", reply_markup=back_menu())
            else:
                await query.edit_message_text("Konfirmasi kedaluwarsa. Mulai lagi dengan /hapusdata.", reply_markup=back_menu())
            return
        if await payments.handle_callback(update, context):
            return
        if await finance.handle_callback(update, context):
            return
        if await markets.handle_callback(update, context):
            return
        await query.edit_message_text("Aksi tidak dikenal. Kembali ke menu utama.", reply_markup=back_menu())
    except Exception:
        logger.exception("Callback gagal", extra={"user_id": update.effective_user.id, "update_id": update.update_id})
        try:
            await query.edit_message_text("Maaf, aksi belum dapat diproses. Silakan coba lagi.", reply_markup=back_menu())
        except Exception:
            pass


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    now = time.monotonic()
    if now - LAST_REQUEST.get(user_id, 0) < 0.35:
        await update.effective_message.reply_text("Terlalu cepat. Tunggu sebentar lalu coba lagi.")
        return
    LAST_REQUEST[user_id] = now
    try:
        if await finance.handle_text(update, context):
            return
        if await markets.on_text_flow(update, context):
            return
        await update.effective_message.reply_text("Pilih menu atau gunakan /help untuk melihat perintah.")
    except Exception:
        logger.exception("Pesan teks gagal", extra={"user_id": user_id, "update_id": update.update_id})
        await update.effective_message.reply_text("Maaf, data belum dapat diproses. Silakan coba lagi.")


async def on_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        await payments.handle_photo(update, context)
    except Exception:
        logger.exception("Foto gagal diproses", extra={"user_id": update.effective_user.id, "update_id": update.update_id})
        await update.effective_message.reply_text("Maaf, foto belum dapat diproses. Silakan coba lagi.")


async def reminders_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    with SessionLocal() as db:
        accounts = list(db.query(Account).filter(Account.telegram_id.is_not(None)).all())
        telegram_ids = [account.telegram_id for account in accounts]
    for telegram_id in telegram_ids:
        if not telegram_id:
            continue
        try:
            state = read_bot_state(telegram_id)
            zone = ZoneInfo(state.get("preferences", {}).get("timezone", "Asia/Jakarta"))
            now = datetime.now(zone)
            today = now.date().isoformat()
            due = []
            for reminder in state.get("reminders", []):
                if not reminder.get("active", True) or reminder.get("last_sent") == today:
                    continue
                snooze = reminder.get("snooze_until")
                frequency = reminder.get("frequency", "bulanan")
                schedule_due = frequency == "harian" or (frequency == "mingguan" and now.isoweekday() == int(reminder.get("day", 1))) or (frequency == "bulanan" and now.day == int(reminder.get("day", 1)))
                is_due = snooze <= today if snooze else now.hour == 9 and schedule_due
                if is_due:
                    due.append(reminder)
            for reminder in due:
                await context.bot.send_message(telegram_id, f"⏰ Pengingat: {escape(reminder['name'])} · {rupiah(reminder['amount'])}. Pilih /pengingat untuk menandai dibayar atau menunda.", parse_mode=ParseMode.HTML)
                from backend.repository import apply_bot_mutation
                def mark_sent(current, reminder_id=reminder["id"]):
                    for item in current.get("reminders", []):
                        if item.get("id") == reminder_id: item["last_sent"] = today
                apply_bot_mutation(telegram_id, mark_sent)
        except Exception:
            logger.exception("Job pengingat gagal", extra={"user_id": telegram_id})


async def post_init(application: Application) -> None:
    Base.metadata.create_all(bind=engine)
    if engine.dialect.name == "postgresql":
        with engine.begin() as connection:
            connection.exec_driver_sql('ALTER TABLE "payments" ENABLE ROW LEVEL SECURITY')
    try:
        await ensure_proof_bucket()
    except Exception:
        logger.exception("Bucket bukti transfer belum siap")
    webapp = WebAppInfo(url=_webapp_url())
    await application.bot.set_chat_menu_button(menu_button=MenuButtonWebApp(text="Doctor Money", web_app=webapp))
    await application.bot.set_my_commands(_commands())
    if application.job_queue:
        application.job_queue.run_repeating(markets.check_alerts, interval=60, first=20, name="price-alerts")
        application.job_queue.run_repeating(reminders_job, interval=60, first=30, name="bill-reminders")
        application.job_queue.run_repeating(payments.payments_job, interval=60, first=40, name="payments")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = getattr(getattr(update, "effective_user", None), "id", None)
    update_id = getattr(update, "update_id", None)
    if isinstance(context.error, FreeLimitReached):
        if isinstance(update, Update) and update.effective_message:
            await update.effective_message.reply_text(f"⚠️ {context.error}\nBuka menu 💎 Upgrade Premium untuk melihat paket.")
        return
    logger.exception("Unhandled Telegram update", extra={"user_id": user, "update_id": update_id})
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text("Terjadi kendala sementara. Data Anda tetap aman; coba lagi sebentar.")
        except Exception:
            pass


def _commands() -> list[BotCommand]:
    return [
        BotCommand("start", "Buka menu utama"), BotCommand("menu", "Tampilkan menu"), BotCommand("catat", "Catat transaksi"),
        BotCommand("riwayat", "Lihat riwayat transaksi"), BotCommand("saldo", "Lihat saldo"), BotCommand("wallet", "Kelola dompet"),
        BotCommand("transfer", "Transfer antar dompet"), BotCommand("budget", "Kelola budget"), BotCommand("laporan", "Laporan bulanan"),
        BotCommand("grafik", "Grafik pengeluaran"), BotCommand("pengingat", "Pengingat tagihan"), BotCommand("simulasi", "Simulasi tabungan"),
        BotCommand("hubungkan", "Hubungkan dashboard"), BotCommand("zona", "Ubah zona waktu"), BotCommand("hapusdata", "Hapus data akun"),
        BotCommand("topcrypto", "10 crypto teratas"), BotCommand("crypto", "Cek harga crypto"), BotCommand("indeks", "Indeks saham"),
        BotCommand("saham", "Cek harga saham"), BotCommand("kurs", "Konversi kurs"), BotCommand("watchlist", "Daftar aset pantauan"),
        BotCommand("alert", "Atur alert harga"), BotCommand("portofolio", "Lihat portofolio"), BotCommand("emas", "Harga emas"),
        BotCommand("feargreed", "Indeks sentimen crypto"),
        BotCommand("help", "Bantuan"),
    ]


def _rate_limited(callback):
    async def run(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user_id = update.effective_user.id if update.effective_user else 0
        now = time.monotonic()
        if now - LAST_REQUEST.get(user_id, 0) < 0.35:
            if update.effective_message:
                await update.effective_message.reply_text("Terlalu cepat. Tunggu sebentar lalu coba lagi.")
            return
        LAST_REQUEST[user_id] = now
        await callback(update, context)
    return run


def build_application() -> Application:
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("BOT_TOKEN wajib diisi")
    _webapp_url()
    application = ApplicationBuilder().token(token).rate_limiter(AIORateLimiter(max_retries=1)).post_init(post_init).build()
    commands = {
        "start": start, "menu": menu_command, "hubungkan": connect_account, "help": help_command,
        "catat": record_command,
        "riwayat": lambda update, context: finance.handle_command(update, context, "history"),
        "saldo": lambda update, context: finance.handle_command(update, context, "balance"),
        "wallet": lambda update, context: finance.handle_command(update, context, "wallet"),
        "transfer": lambda update, context: finance.handle_command(update, context, "transfer"),
        "budget": lambda update, context: finance.handle_command(update, context, "budget"),
        "laporan": lambda update, context: finance.handle_command(update, context, "report"),
        "grafik": lambda update, context: finance.handle_command(update, context, "chart"),
        "pengingat": lambda update, context: finance.handle_command(update, context, "reminders"),
        "simulasi": lambda update, context: finance.handle_command(update, context, "simulation"),
        "topcrypto": lambda update, context: market_command(update, context, "top_crypto"),
        "crypto": lambda update, context: market_command(update, context, "check_crypto", "crypto"),
        "indeks": lambda update, context: market_command(update, context, "stock_index"),
        "saham": lambda update, context: market_command(update, context, "check_stock", "stock"),
        "kurs": lambda update, context: market_command(update, context, "currency", "currency"),
        "watchlist": lambda update, context: market_command(update, context, "watchlist"),
        "alert": alert_command,
        "portofolio": lambda update, context: market_command(update, context, "portfolio"),
        "emas": lambda update, context: market_command(update, context, "gold"),
        "feargreed": lambda update, context: market_command(update, context, "fear_greed"),
        "zona": timezone_command, "hapusdata": delete_data,
    }
    for command, callback in commands.items():
        application.add_handler(CommandHandler(command, _rate_limited(callback)))
    application.add_handler(CallbackQueryHandler(on_callback))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    application.add_handler(MessageHandler(filters.PHOTO, on_photo))
    application.add_error_handler(error_handler)
    return application


async def record_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await finance.show_record(update, context, " ".join(context.args) if context.args else None)


async def alert_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if context.args:
        context.user_data["market_flow"] = "alert:add"
        await markets.on_text_flow(update, context)
    else:
        await markets.show_menu(update, context, "alerts")


async def market_command(update: Update, context: ContextTypes.DEFAULT_TYPE, key: str, flow: str | None = None) -> None:
    if flow and context.args:
        context.user_data["market_flow"] = flow
        await markets.on_text_flow(update, context)
    else:
        await markets.show_menu(update, context, key)


def main() -> None:
    mode = os.getenv("MODE", "polling").lower()
    application = build_application()
    if mode == "polling":
        application.run_polling(allowed_updates=Update.ALL_TYPES)
    elif mode == "webhook":
        webhook_url = os.getenv("WEBHOOK_URL", "").strip()
        if not webhook_url.startswith("https://"):
            raise RuntimeError("WEBHOOK_URL wajib HTTPS untuk mode webhook")
        secret = re.sub(r"[^A-Za-z0-9_-]", "", hashlib.sha256(os.getenv("APP_SECRET", "").encode()).hexdigest())[:40]
        if not os.getenv("APP_SECRET"):
            raise RuntimeError("APP_SECRET wajib diisi untuk webhook")
        path = "/telegram/" + hashlib.sha256(os.getenv("BOT_TOKEN", "").encode()).hexdigest()[:20]
        application.run_webhook(listen="0.0.0.0", port=int(os.getenv("PORT", "8080")), url_path=path, webhook_url=webhook_url.rstrip("/") + path, secret_token=secret, allowed_updates=Update.ALL_TYPES)
    else:
        raise RuntimeError("MODE harus polling atau webhook")


if __name__ == "__main__":
    main()