import re
import uuid
from datetime import datetime, timezone
from html import escape

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from backend.database import SessionLocal
from backend.models import Account
from backend.repository import apply_bot_mutation, read_bot_state
from bot.keyboards import back_menu
from bot.services import market
from bot.utils.formatting import rupiah


def _id(update: Update) -> int:
    return update.effective_user.id


def _money(value: float | int) -> str:
    return rupiah(round(value))


async def _reply(update: Update, text: str, markup=None, edit: bool = False) -> None:
    if edit and update.callback_query:
        await update.callback_query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=markup)
    else:
        await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=markup)


def _market_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️ Kembali ke Menu", callback_data="menu:home")],
    ])


async def show_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, key: str, edit: bool = False) -> bool:
    if key not in {"top_crypto", "check_crypto", "stock_index", "check_stock", "currency", "fear_greed", "gold", "watchlist", "alerts", "portfolio"}:
        return False
    if key == "top_crypto":
        try:
            coins = await market.top_crypto()
            lines = ["<b>Top Crypto berdasarkan kapitalisasi pasar</b>"]
            for item in coins:
                change = item.get("price_change_percentage_24h")
                delta = "–" if change is None else f"{change:+.2f}%"
                lines.append(f"• {escape(item.get('name', ''))} ({escape(item.get('symbol', '').upper())}): {_money(item.get('current_price') or 0)} · 24 jam {delta}")
            text = "\n".join(lines) + "\n\n" + market.DISCLAIMER
        except Exception:
            text = market.UNAVAILABLE + "\n\n" + market.DISCLAIMER
        await _reply(update, text, _market_menu(), edit)
        return True
    if key == "check_crypto":
        context.user_data["market_flow"] = "crypto"
        await _reply(update, "Kirim simbol atau nama crypto, misalnya <code>BTC</code>.", back_menu(), edit)
        return True
    if key in {"stock_index", "check_stock"}:
        if key == "check_stock":
            context.user_data["market_flow"] = "stock"
            await _reply(update, "Kirim kode saham, misalnya BBCA.JK. Sumber data IDX belum dipilih; saya tidak akan memakai endpoint tidak resmi.\n\n" + market.DISCLAIMER, back_menu(), edit)
        else:
            await _reply(update, "Data IHSG belum ditampilkan karena sumber IDX gratis yang legal dan stabil belum dipilih. Opsi: Alpha Vantage (kuota gratis, API key), Twelve Data (kuota terbatas/berbayar), atau lisensi data resmi BEI.\n\n" + market.DISCLAIMER, back_menu(), edit)
        return True
    if key == "currency":
        context.user_data["market_flow"] = "currency"
        await _reply(update, "Kurs default USD ke IDR. Kirim nominal dan kode mata uang, misalnya <code>100 USD IDR</code>.", back_menu(), edit)
        return True
    if key == "fear_greed":
        try:
            current, previous = await market.fear_greed()
            label = {"Extreme Fear": "Sangat takut", "Fear": "Takut", "Neutral": "Netral", "Greed": "Serakah", "Extreme Greed": "Sangat serakah"}.get(current.get("value_classification"), "Tidak diketahui")
            delta = int(current["value"]) - int(previous["value"]) if previous else 0
            text = f"<b>Crypto Fear &amp; Greed Index</b>\nNilai: {current['value']}/100 · {label}\nPerubahan dari kemarin: {delta:+d} poin\nSumber: alternative.me\n\n{market.DISCLAIMER}"
        except Exception:
            text = market.UNAVAILABLE + "\n\n" + market.DISCLAIMER
        await _reply(update, text, _market_menu(), edit)
        return True
    if key == "gold":
        await _reply(update, "Sumber harga emas per gram IDR belum dipilih. Opsi: API harga emas berlisensi (umumnya berbayar), atau input harga manual dengan sumber dan waktu pembaruan tercatat.\n\n" + market.DISCLAIMER, _market_menu(), edit)
        return True
    if key == "watchlist":
        await show_watchlist(update, edit)
        return True
    if key == "alerts":
        await show_alerts(update, edit)
        return True
    if key == "portfolio":
        await show_portfolio(update, edit)
        return True
    return False


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    flow = context.user_data.get("market_flow")
    if not flow:
        return False
    text = (update.effective_message.text or "")[:120].strip()
    user_id = _id(update)
    if flow == "crypto":
        context.user_data.pop("market_flow", None)
        try:
            coin = await market.find_crypto(text)
            change7 = coin.get("price_change_percentage_7d_in_currency")
            change24 = coin.get("price_change_percentage_24h")
            price_idr = coin.get("current_price") or 0
            price_usd = await market._get("https://api.coingecko.com/api/v3/coins/" + coin["id"], {"localization": "false", "tickers": "false", "market_data": "true", "community_data": "false", "developer_data": "false"})
            usd = price_usd.get("market_data", {}).get("current_price", {}).get("usd", 0)
            cap = coin.get("market_cap") or 0
            text_out = f"<b>{escape(coin.get('name', ''))} ({escape(coin.get('symbol', '').upper())})</b>\nHarga: {_money(price_idr)} · US$ {usd:,.4f}\nPerubahan 24 jam: {'–' if change24 is None else f'{change24:+.2f}%'}\nPerubahan 7 hari: {'–' if change7 is None else f'{change7:+.2f}%'}\nKapitalisasi: {_money(cap)}\nSumber: CoinGecko\n\n{market.DISCLAIMER}"
        except Exception:
            text_out = market.UNAVAILABLE + "\n\n" + market.DISCLAIMER
        await update.effective_message.reply_text(text_out, parse_mode=ParseMode.HTML, reply_markup=_market_menu())
        return True
    if flow == "stock":
        context.user_data.pop("market_flow", None)
        try:
            await market.stock_data(text)
        except Exception as exc:
            await update.effective_message.reply_text(escape(str(exc)) + "\n\n" + market.DISCLAIMER, parse_mode=ParseMode.HTML, reply_markup=_market_menu())
        return True
    if flow == "currency":
        context.user_data.pop("market_flow", None)
        parts = text.split()
        try:
            amount = float(parts[0].replace(",", ".")) if parts else 1
            base, quote = (parts[1].upper(), parts[2].upper()) if len(parts) >= 3 else ("USD", "IDR")
            if len(parts) == 2:
                base, quote = parts[1].upper(), "IDR"
            if amount <= 0 or amount > 1e15 or not re.fullmatch(r"[A-Z]{3}", base + "") or not re.fullmatch(r"[A-Z]{3}", quote + ""):
                raise ValueError
            rate = await market.exchange_rate(base, quote)
            amount_out = amount * rate
            text_out = f"<b>Kurs {amount:g} {base} ke {quote}</b>\n1 {base} = {rate:,.4f} {quote}\nHasil: {_money(amount_out) if quote == 'IDR' else f'{amount_out:,.2f} {quote}'}\nSumber: Frankfurter (data referensi ECB)\n\n{market.DISCLAIMER}"
        except Exception:
            text_out = market.UNAVAILABLE + "\n\n" + market.DISCLAIMER
        await update.effective_message.reply_text(text_out, parse_mode=ParseMode.HTML, reply_markup=_market_menu())
        return True
    return False


async def show_watchlist(update: Update, edit: bool = False) -> None:
    state = read_bot_state(_id(update))
    rows = []
    lines = ["<b>Watchlist</b>"]
    for item in state.get("watchlist", []):
        code = item["code"]
        try:
            coin = await market.find_crypto(code)
            lines.append(f"• {escape(code)}: {_money(coin.get('current_price') or 0)}")
        except Exception:
            lines.append(f"• {escape(code)}: harga belum tersedia")
        rows.append([InlineKeyboardButton("Hapus " + code[:15], callback_data="watch:remove:" + item["id"])])
    if not state.get("watchlist"):
        lines.append("Belum ada aset dalam daftar.")
    rows.append([InlineKeyboardButton("➕ Tambah aset", callback_data="watch:add")])
    rows.append([InlineKeyboardButton("⬅️ Kembali ke Menu", callback_data="menu:home")])
    await _reply(update, "\n".join(lines) + "\n\n" + market.DISCLAIMER, InlineKeyboardMarkup(rows), edit)


async def show_alerts(update: Update, edit: bool = False) -> None:
    state = read_bot_state(_id(update))
    rows = []
    lines = ["<b>Alert Harga</b>"]
    for alert in state.get("alerts", []):
        lines.append(f"• {escape(alert['code'])} {escape(alert['direction'])} {_money(alert['threshold'])} · {'aktif' if alert.get('active') else 'nonaktif'}")
        if alert.get("active"):
            rows.append([InlineKeyboardButton("Nonaktifkan " + alert["code"][:15], callback_data="alert:remove:" + alert["id"])])
    if not state.get("alerts"):
        lines.append("Belum ada alert. Contoh: BTC > 70000")
    rows.append([InlineKeyboardButton("➕ Buat alert", callback_data="alert:add")])
    rows.append([InlineKeyboardButton("⬅️ Kembali ke Menu", callback_data="menu:home")])
    await _reply(update, "\n".join(lines) + "\n\n" + market.DISCLAIMER, InlineKeyboardMarkup(rows), edit)


async def show_portfolio(update: Update, edit: bool = False) -> None:
    state = read_bot_state(_id(update))
    assets = state.get("portfolio", [])
    lines = ["<b>Portofolio</b>"]
    total_value = 0.0
    total_cost = 0.0
    for item in assets:
        try:
            coin = await market.find_crypto(item["code"])
            price = float(coin.get("current_price") or 0)
            if item.get("currency") == "USD":
                price = float((await market._get("https://api.coingecko.com/api/v3/coins/" + coin["id"], {"localization": "false", "tickers": "false", "market_data": "true", "community_data": "false", "developer_data": "false"})).get("market_data", {}).get("current_price", {}).get("usd", 0))
            value = item["quantity"] * price
            cost = item["quantity"] * item["purchase_price"]
            total_value += value
            total_cost += cost
            lines.append(f"• {escape(item['code'])} {item['quantity']:g}: nilai {_money(value)} · modal {_money(cost)}")
        except Exception:
            lines.append(f"• {escape(item['code'])}: harga terkini belum tersedia")
    if not assets:
        lines.append("Belum ada aset. Format catat: BTC 0,02 60000")
    else:
        lines.append(f"\nNilai terkini: {_money(total_value)}\nUntung/rugi: {'+' if total_value >= total_cost else '−'}{_money(abs(total_value-total_cost))} · {((total_value-total_cost)/total_cost*100 if total_cost else 0):+.2f}%")
    rows = [[InlineKeyboardButton("➕ Catat aset", callback_data="portfolio:add")], [InlineKeyboardButton("⬅️ Kembali ke Menu", callback_data="menu:home")]]
    await _reply(update, "\n".join(lines) + "\n\n" + market.DISCLAIMER, InlineKeyboardMarkup(rows), edit)


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    data = update.callback_query.data or ""
    user_id = _id(update)
    if data in {"watch:add", "alert:add", "portfolio:add"}:
        flow = {"watch:add": "watchlist:add", "alert:add": "alert:add", "portfolio:add": "portfolio:add"}[data]
        context.user_data["market_flow"] = flow
        prompt = {"watch:add": "Kirim simbol crypto, misalnya BTC.", "alert:add": "Kirim syarat, misalnya BTC > 70000 atau BTC di atas 1200000000.", "portfolio:add": "Kirim kode jumlah harga_beli, misalnya BTC 0,02 60000."}[data]
        await update.callback_query.edit_message_text(prompt, reply_markup=back_menu())
        return True
    if data.startswith("watch:remove:"):
        item_id = data.split(":", 2)[2]
        apply_bot_mutation(user_id, lambda state: state.update(watchlist=[item for item in state.get("watchlist", []) if item.get("id") != item_id]))
        await show_watchlist(update, edit=True)
        return True
    if data.startswith("alert:remove:"):
        item_id = data.split(":", 2)[2]
        def deactivate(state):
            for alert in state.get("alerts", []):
                if alert.get("id") == item_id: alert["active"] = False
        apply_bot_mutation(user_id, deactivate)
        await show_alerts(update, edit=True)
        return True
    return False


async def on_text_flow(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    flow = context.user_data.get("market_flow")
    if flow not in {"watchlist:add", "alert:add"}:
        return False
    text = (update.effective_message.text or "")[:100].strip()
    user_id = _id(update)
    if flow == "alert:add" and text.lower().startswith("/alert"):
        text = text[len("/alert"):].strip()
    elif flow == "crypto" and text.lower().startswith("/crypto"):
        text = text[len("/crypto"):].strip()
    elif flow == "stock" and text.lower().startswith("/saham"):
        text = text[len("/saham"):].strip()
    elif flow == "currency" and text.lower().startswith("/kurs"):
        text = text[len("/kurs"):].strip()
    if flow == "watchlist:add":
        code = text.upper()
        try:
            coin = await market.find_crypto(code)
        except Exception:
            await update.effective_message.reply_text("Crypto tidak ditemukan atau sumber sedang tidak tersedia.")
            return True
        def save(state):
            items = state.setdefault("watchlist", [])
            if not any(item.get("code") == code for item in items):
                items.append({"id": "w" + uuid.uuid4().hex[:10], "code": code, "asset_id": coin["id"], "kind": "crypto"})
        apply_bot_mutation(user_id, save)
        context.user_data.pop("market_flow", None)
        await update.effective_message.reply_text("Aset ditambahkan ke watchlist.\n\n" + market.DISCLAIMER, reply_markup=_market_menu())
        return True
    match = re.fullmatch(r"([A-Za-z0-9_-]{1,15})\s*(?:di\s+atas|>)\s*([\d.,]+)", text, re.I)
    if not match:
        await update.effective_message.reply_text("Format alert belum sesuai. Contoh: BTC > 70000")
        return True
    code, threshold_text = match.groups()
    threshold = float(threshold_text.replace(".", "").replace(",", ".") if "." in threshold_text and "," not in threshold_text and len(threshold_text.split(".")[-1]) == 3 else threshold_text.replace(",", "."))
    try:
        coin = await market.find_crypto(code)
    except Exception:
        await update.effective_message.reply_text("Crypto tidak ditemukan atau sumber sedang tidak tersedia.")
        return True
    currency = "idr" if threshold >= 1_000_000 else "usd"
    alert = {"id": "a" + uuid.uuid4().hex[:10], "code": code.upper(), "asset_id": coin["id"], "direction": ">", "threshold": threshold, "currency": currency, "active": True, "created_at": datetime.now(timezone.utc).isoformat()}
    apply_bot_mutation(user_id, lambda state: state.setdefault("alerts", []).append(alert))
    context.user_data.pop("market_flow", None)
    await update.effective_message.reply_text("Alert harga aktif.\n\n" + market.DISCLAIMER, reply_markup=_market_menu())
    return True


async def check_alerts(context: ContextTypes.DEFAULT_TYPE) -> None:
    with SessionLocal() as db:
        accounts = list(db.query(Account).filter(Account.telegram_id.is_not(None)).all())
        users = [account.telegram_id for account in accounts]
    for user_id in users:
        if not user_id:
            continue
        try:
            state = read_bot_state(user_id)
            active = [alert for alert in state.get("alerts", []) if alert.get("active")]
            for alert in active:
                currency = alert.get("currency", "usd")
                coins = await market._get("https://api.coingecko.com/api/v3/coins/markets", {"vs_currency": currency, "ids": alert["asset_id"]}, ttl=30)
                if coins and float(coins[0].get("current_price") or 0) > float(alert["threshold"]):
                    def deactivate(current):
                        for item in current.get("alerts", []):
                            if item.get("id") == alert["id"]: item["active"] = False
                    apply_bot_mutation(user_id, deactivate)
                    threshold = f"US$ {alert['threshold']:,.2f}" if currency == "usd" else _money(alert["threshold"])
                    await context.bot.send_message(user_id, f"🔔 {escape(alert['code'])} melampaui {threshold}.\n\n{market.DISCLAIMER}", parse_mode=ParseMode.HTML)
        except Exception:
            continue