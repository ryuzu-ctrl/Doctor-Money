from telegram import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo


MENU_ITEMS = (
    ("top_crypto", "₿ Top Crypto"), ("stock_index", "📈 Indeks Saham"),
    ("check_crypto", "🔎 Cek Crypto"), ("check_stock", "🔎 Cek Saham"),
    ("currency", "💱 Kurs"), ("watchlist", "⭐ Watchlist"),
    ("alerts", "🔔 Alert Harga"), ("report", "📊 Laporan"),
    ("record", "📝 Catat Keuangan"), ("history", "🕘 Riwayat"),
    ("balance", "💼 Saldo"), ("wallet", "👛 Wallet"),
    ("transfer", "🔁 Transfer"), ("reminders", "⏰ Pengingat"),
    ("budget", "🎯 Budget"), ("portfolio", "💼 Portofolio"),
    ("fear_greed", "🧭 Fear & Greed"), ("gold", "🥇 Emas"),
    ("chart", "📉 Grafik"), ("simulation", "🧮 Simulasi"),
    ("connect", "🔗 Hubungkan Akun"), ("help", "❓ Bantuan"),
)


def main_menu(webapp_url: str) -> InlineKeyboardMarkup:
    rows = []
    if webapp_url.startswith("https://"):
        rows.append([InlineKeyboardButton("🌐 Buka Web App", web_app=WebAppInfo(url=webapp_url))])
    for index in range(0, len(MENU_ITEMS), 2):
        first = MENU_ITEMS[index]
        second = MENU_ITEMS[index + 1]
        rows.append([
            InlineKeyboardButton(first[1], callback_data=f"menu:{first[0]}"),
            InlineKeyboardButton(second[1], callback_data=f"menu:{second[0]}"),
        ])
    return InlineKeyboardMarkup(rows)


def back_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Kembali ke Menu", callback_data="menu:home")]])


def menu_keyboard(webapp_url: str) -> InlineKeyboardMarkup:
    return main_menu(webapp_url)


def history_keyboard(page: int, has_next: bool, filter_name: str = "all", delete_ids: list[str] | None = None):
    row = []
    if page > 0:
        row.append(InlineKeyboardButton("◀ Sebelumnya", callback_data=f"history:{page - 1}:{filter_name}"))
    if has_next:
        row.append(InlineKeyboardButton("Berikutnya ▶", callback_data=f"history:{page + 1}:{filter_name}"))
    rows = [row] if row else []
    rows.append([
        InlineKeyboardButton("Semua", callback_data="history:0:all"),
        InlineKeyboardButton("Pemasukan", callback_data="history:0:in"),
        InlineKeyboardButton("Pengeluaran", callback_data="history:0:out"),
    ])
    for transaction_id in delete_ids or []:
        rows.append([InlineKeyboardButton("🗑 Hapus " + transaction_id[-8:], callback_data=f"delete:{transaction_id}")])
    rows.append([InlineKeyboardButton("⬅️ Kembali ke Menu", callback_data="menu:home")])
    return InlineKeyboardMarkup(rows)