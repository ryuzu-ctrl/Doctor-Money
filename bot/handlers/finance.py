import re
import uuid
from datetime import datetime, timedelta
from html import escape
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from backend.repository import apply_bot_mutation, read_bot_state
from bot.keyboards import back_menu, history_keyboard
from bot.services.finance import CATEGORIES, dashboard_score, parse_amount, parse_transaction
from bot.utils.formatting import date_id, rupiah, signed_rupiah


def _user_id(update: Update) -> int:
    return update.effective_user.id


def _zone(state: dict) -> ZoneInfo:
    name = state.get("preferences", {}).get("timezone", "Asia/Jakarta")
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo("Asia/Jakarta")


def _today(state: dict) -> str:
    return datetime.now(_zone(state)).date().isoformat()


def _money(amount: int) -> str:
    return rupiah(amount)


def _wallet_name(state: dict, wallet_id: str) -> str:
    return next((wallet.get("n", "Dompet") for wallet in state.get("wallets", []) if wallet.get("id") == wallet_id), "Dompet")


def _transaction_keyboard(parsed: dict, state: dict) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Simpan", callback_data="record:save"), InlineKeyboardButton("Ubah kategori", callback_data="record:categories")],
        [InlineKeyboardButton("Pilih dompet", callback_data="record:wallets"), InlineKeyboardButton("Batal", callback_data="record:cancel")],
    ])


async def show_record(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str | None = None) -> None:
    context.user_data["finance_flow"] = "record"
    if text:
        parsed = parse_transaction(text[:300])
        if not parsed:
            await update.effective_message.reply_text("Nominal belum terbaca. Contoh: kopi 25.000, bensin 50rb, atau gaji 9jt.")
            return
        state = read_bot_state(_user_id(update))
        context.user_data["pending_transaction"] = parsed
        wallet = state.get("wallets", [{}])[0]
        context.user_data["pending_wallet"] = wallet.get("id", "main")
        await update.effective_message.reply_text(
            "<b>Pratinjau transaksi</b>\n"
            f"{escape(parsed['description'])}\n"
            f"{escape(CATEGORIES[parsed['category']]['name'])} · {escape(wallet.get('n', 'Dompet'))}\n"
            f"{'+' if parsed['type'] == 'in' else '−'}{_money(parsed['amount'])}",
            parse_mode=ParseMode.HTML,
            reply_markup=_transaction_keyboard(parsed, state),
        )
        return
    await update.effective_message.reply_text("Kirim transaksi, misalnya <code>kopi 25.000</code>, <code>beli buku 1,5jt</code>, <code>bensin 50rb</code>, atau <code>gaji 9jt</code>.", parse_mode=ParseMode.HTML)


async def show_balance(update: Update, context: ContextTypes.DEFAULT_TYPE, edit: bool = False) -> None:
    state = read_bot_state(_user_id(update))
    balances = []
    total = 0
    for wallet in state.get("wallets", []):
        amount = int(wallet.get("start", 0))
        for tx in state.get("txs", []):
            if tx.get("w") == wallet["id"]:
                amount += int(tx.get("amt", 0)) * (1 if tx.get("type") == "in" else -1)
        total += amount
        balances.append(f"• {escape(wallet.get('n', 'Dompet'))}: {signed_rupiah(amount)}")
    text = "<b>Total saldo</b>\n" + "\n".join(balances) + f"\n\n<b>{signed_rupiah(total)}</b>"
    if edit and update.callback_query:
        await update.callback_query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=back_menu())
    else:
        await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=back_menu())


def _wallet_keyboard(state: dict) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(wallet.get("n", "Dompet")[:30], callback_data="wallet:edit:" + wallet["id"])] for wallet in state.get("wallets", [])]
    rows.append([InlineKeyboardButton("➕ Tambah dompet", callback_data="wallet:add")])
    rows.append([InlineKeyboardButton("⬅️ Kembali ke Menu", callback_data="menu:home")])
    return InlineKeyboardMarkup(rows)


async def show_wallets(update: Update, context: ContextTypes.DEFAULT_TYPE, edit: bool = False) -> None:
    state = read_bot_state(_user_id(update))
    message = "<b>Wallet</b>\nPilih dompet untuk mengubah nama atau saldo awal."
    if edit and update.callback_query:
        await update.callback_query.edit_message_text(message, parse_mode=ParseMode.HTML, reply_markup=_wallet_keyboard(state))
    else:
        await update.effective_message.reply_text(message, parse_mode=ParseMode.HTML, reply_markup=_wallet_keyboard(state))


def _history_text(state: dict, page: int, filter_type: str) -> tuple[str, bool, list[str]]:
    txs = sorted(state.get("txs", []), key=lambda tx: (tx.get("date", ""), tx.get("id", "")), reverse=True)
    if filter_type in {"in", "out"}:
        txs = [tx for tx in txs if tx.get("type") == filter_type]
    start = page * 10
    selected = txs[start:start + 10]
    if not selected:
        return "Belum ada transaksi pada filter ini.", False, []
    lines = ["<b>Riwayat Transaksi</b>"]
    for tx in selected:
        sign = "+" if tx.get("type") == "in" else "−"
        lines.append(f"{date_id(tx.get('date', _today(state)))} · {sign}{_money(tx.get('amt', 0))}\n{escape(tx.get('desc', 'Transaksi'))} · {escape(CATEGORIES.get(tx.get('cat'), {}).get('name', 'Lainnya'))} · {escape(_wallet_name(state, tx.get('w', '')))}")
    return "\n\n".join(lines), start + 10 < len(txs), [str(tx.get("id", "")) for tx in selected]


async def show_history(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 0, filter_type: str = "all", edit: bool = False) -> None:
    state = read_bot_state(_user_id(update))
    text, has_next, delete_ids = _history_text(state, max(0, page), filter_type)
    markup = history_keyboard(max(0, page), has_next, filter_type, delete_ids)
    if edit and update.callback_query:
        await update.callback_query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=markup)
    else:
        await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=markup)


async def show_report(update: Update, context: ContextTypes.DEFAULT_TYPE, year_month: str | None = None, edit: bool = False) -> None:
    state = read_bot_state(_user_id(update))
    now = datetime.now(_zone(state))
    month = year_month or now.strftime("%Y-%m")
    report = dashboard_score(state, month)
    names = {"Prima": "Prima", "Sehat": "Sehat", "Perlu perhatian": "Perlu perhatian", "Kritis": "Kritis"}
    score = report["score"]
    score_label = "Belum ada data" if score is None else names["Prima" if score >= 80 else "Sehat" if score >= 60 else "Perlu perhatian" if score >= 40 else "Kritis"]
    bars = "▰" * min(10, round(report["budget_used"] * 10)) + "▱" * (10 - min(10, round(report["budget_used"] * 10)))
    categories = report["top_categories"] or []
    cat_text = "\n".join(f"• {escape(CATEGORIES.get(key, {}).get('name', key))}: {_money(amount)}" for key, amount in categories) or "Belum ada pengeluaran"
    ratio = "–" if report["savings_ratio"] is None else f"{report['savings_ratio'] * 100:.1f}%"
    text = (f"<b>Laporan {escape(month)}</b>\nPemasukan: {_money(report['income'])}\nPengeluaran: {_money(report['expenses'])}\nSelisih: {signed_rupiah(report['net'])}\nRasio tabungan: {ratio}\nBudget terpakai: {bars}\nSkor kesehatan: {score if score is not None else '–'}/100 · {score_label}\n\n<b>5 kategori teratas</b>\n{cat_text}")
    year, mon = map(int, month.split("-"))
    prev = f"{year - (mon == 1):04d}-{12 if mon == 1 else mon - 1:02d}"
    nxt = f"{year + (mon == 12):04d}-{1 if mon == 12 else mon + 1:02d}"
    markup = InlineKeyboardMarkup([[InlineKeyboardButton("◀ Bulan", callback_data="report:" + prev), InlineKeyboardButton("Bulan ▶", callback_data="report:" + nxt)], [InlineKeyboardButton("⬅️ Kembali ke Menu", callback_data="menu:home")]])
    if edit and update.callback_query:
        await update.callback_query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=markup)
    else:
        await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=markup)


async def show_budget(update: Update, context: ContextTypes.DEFAULT_TYPE, edit: bool = False) -> None:
    state = read_bot_state(_user_id(update))
    month = datetime.now(_zone(state)).strftime("%Y-%m")
    report = dashboard_score(state, month)
    used_by_cat: dict[str, int] = {}
    for tx in state.get("txs", []):
        if tx.get("date", "").startswith(month) and tx.get("type") == "out" and not tx.get("sv"):
            used_by_cat[tx.get("cat", "lain")] = used_by_cat.get(tx.get("cat", "lain"), 0) + int(tx.get("amt", 0))
    lines = [f"<b>Budget {month}</b>"]
    buttons = []
    for key, limit in state.get("budgets", {}).items():
        spent = used_by_cat.get(key, 0)
        ratio = spent / limit if limit else 1
        status = "Melebihi" if ratio >= 1 else "Hampir habis" if ratio >= .8 else "Aman"
        blocks = min(10, round(ratio * 10))
        lines.append(f"• {escape(CATEGORIES.get(key, {}).get('name', key))}: {_money(spent)} / {_money(limit)}\n{'▰' * blocks}{'▱' * (10 - blocks)} · {status}")
        buttons.append(InlineKeyboardButton(CATEGORIES.get(key, {}).get("name", key), callback_data="budget:set:" + key))
    if not state.get("budgets"):
        lines.append("Belum ada batas budget. Tambahkan kategori di bawah.")
    rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    rows.append([InlineKeyboardButton("➕ Atur budget kategori", callback_data="budget:choose")])
    rows.append([InlineKeyboardButton("⬅️ Kembali ke Menu", callback_data="menu:home")])
    text = "\n\n".join(lines) + f"\n\nSisa budget total: {_money(sum(state.get('budgets', {}).values()) - sum(used_by_cat.values()))}"
    if edit and update.callback_query:
        await update.callback_query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(rows))
    else:
        await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(rows))


async def show_reminders(update: Update, context: ContextTypes.DEFAULT_TYPE, edit: bool = False) -> None:
    state = read_bot_state(_user_id(update))
    reminders = state.get("reminders", [])
    lines = ["<b>Pengingat Tagihan</b>"]
    rows = []
    for reminder in reminders:
        lines.append(f"• {escape(reminder['name'])}: {_money(reminder['amount'])} · tanggal {reminder['day']} · {escape(reminder['frequency'])}")
        if reminder.get("active", True):
            rows.append([InlineKeyboardButton("✅ Dibayar: " + reminder["name"][:20], callback_data="reminder:paid:" + reminder["id"]), InlineKeyboardButton("Tunda 1 hari", callback_data="reminder:snooze:" + reminder["id"])])
    if not reminders:
        lines.append("Belum ada pengingat. Tambahkan dengan format: PLN 420000 5 bulanan")
    rows.append([InlineKeyboardButton("➕ Tambah pengingat", callback_data="reminder:add")])
    rows.append([InlineKeyboardButton("⬅️ Kembali ke Menu", callback_data="menu:home")])
    markup = InlineKeyboardMarkup(rows)
    if edit and update.callback_query:
        await update.callback_query.edit_message_text("\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=markup)
    else:
        await update.effective_message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=markup)


async def show_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, key: str) -> bool:
    message = update.effective_message
    query = update.callback_query
    if key == "record":
        if query:
            context.user_data["finance_flow"] = "record"
            await query.edit_message_text("Kirim transaksi, misalnya <code>kopi 25.000</code> atau <code>gaji 9jt</code>.", parse_mode=ParseMode.HTML, reply_markup=back_menu())
        else:
            await show_record(update, context)
        return True
    if key in {"history", "balance", "wallet", "transfer", "budget", "report", "reminders", "simulation", "chart"}:
        if key == "history":
            await show_history(update, context, edit=bool(query))
        elif key == "balance":
            await show_balance(update, context, edit=bool(query))
        elif key == "wallet":
            await show_wallets(update, context, edit=bool(query))
        elif key == "transfer":
            state = read_bot_state(_user_id(update))
            rows = [[InlineKeyboardButton(wallet.get("n", "Dompet")[:30], callback_data="transfer:from:" + wallet["id"])] for wallet in state.get("wallets", [])]
            rows.append([InlineKeyboardButton("⬅️ Kembali ke Menu", callback_data="menu:home")])
            text = "<b>Transfer antar dompet</b>\nPilih dompet asal."
            if query: await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(rows))
            else: await message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(rows))
        elif key == "budget":
            await show_budget(update, context, edit=bool(query))
        elif key == "report":
            await show_report(update, context, edit=bool(query))
        elif key == "reminders":
            await show_reminders(update, context, edit=bool(query))
        elif key == "simulation":
            context.user_data["finance_flow"] = "simulation"
            text = "Kirim simulasi tabungan: <code>1000000 8 10</code> (setoran/bulan, bunga tahunan %, tahun), atau simulasi cicilan: <code>cicilan 100000000 9 10</code> (pokok, bunga tahunan %, tahun)."
            if query: await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=back_menu())
            else: await message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=back_menu())
        elif key == "chart":
            await send_chart(update, context, edit=bool(query))
        return True
    return False


async def send_chart(update: Update, context: ContextTypes.DEFAULT_TYPE, edit: bool = False) -> None:
    from bot.services.charts import expense_charts
    state = read_bot_state(_user_id(update))
    try:
        week, categories = expense_charts(state)
        if edit and update.callback_query:
            from telegram import InputMediaPhoto
            await update.callback_query.edit_message_media(InputMediaPhoto(week, caption="Pengeluaran 7 hari terakhir."), reply_markup=back_menu())
            await context.bot.send_photo(update.effective_chat.id, categories, caption="Sebaran kategori.\n\n", reply_markup=back_menu())
        else:
            await update.effective_message.reply_photo(week, caption="Pengeluaran 7 hari terakhir.")
            await update.effective_message.reply_photo(categories, caption="Sebaran kategori.", reply_markup=back_menu())
    except Exception:
        await update.effective_message.reply_text("Grafik belum dapat dibuat saat ini.", reply_markup=back_menu())


async def handle_command(update: Update, context: ContextTypes.DEFAULT_TYPE, key: str) -> None:
    await show_menu(update, context, key)


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    query = update.callback_query
    data = query.data or ""
    user_id = _user_id(update)
    if data.startswith("history:"):
        _, page, filter_type = data.split(":", 2)
        await show_history(update, context, int(page), filter_type, edit=True)
        return True
    if data.startswith("report:"):
        await show_report(update, context, data.split(":", 1)[1], edit=True)
        return True
    if data.startswith("record:"):
        action = data.split(":", 1)[1]
        if action == "cancel":
            context.user_data.pop("pending_transaction", None)
            context.user_data.pop("finance_flow", None)
            await query.edit_message_text("Pencatatan dibatalkan.", reply_markup=back_menu())
        elif action == "save":
            parsed = context.user_data.get("pending_transaction")
            if not parsed:
                await query.edit_message_text("Pratinjau sudah tidak tersedia. Silakan catat ulang.", reply_markup=back_menu())
                return True
            state, tx_id = apply_bot_mutation(user_id, lambda state: _save_pending(state, parsed, context.user_data.get("pending_wallet")))
            context.user_data.pop("pending_transaction", None)
            context.user_data.pop("finance_flow", None)
            tx = next(tx for tx in state["txs"] if tx["id"] == tx_id)
            await query.edit_message_text(f"Tersimpan: {escape(tx['desc'])} · {_money(tx['amt'])}", parse_mode=ParseMode.HTML, reply_markup=back_menu())
        elif action == "categories":
            parsed = context.user_data.get("pending_transaction", {})
            keys = [key for key, item in CATEGORIES.items() if item["type"] == parsed.get("type")]
            rows = [[InlineKeyboardButton(CATEGORIES[key]["name"], callback_data="record:category:" + key) for key in keys[i:i + 2]] for i in range(0, len(keys), 2)]
            rows.append([InlineKeyboardButton("⬅️ Kembali", callback_data="record:preview")])
            await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(rows))
        elif action.startswith("category:"):
            key = action.split(":", 1)[1]
            parsed = context.user_data.get("pending_transaction")
            if parsed and key in CATEGORIES and CATEGORIES[key]["type"] == parsed["type"]:
                parsed["category"] = key
            await _refresh_preview(query, context)
        elif action == "wallets":
            state = read_bot_state(user_id)
            rows = [[InlineKeyboardButton(wallet.get("n", "Dompet")[:30], callback_data="record:wallet:" + wallet["id"])] for wallet in state.get("wallets", [])]
            rows.append([InlineKeyboardButton("⬅️ Kembali", callback_data="record:preview")])
            await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(rows))
        elif action.startswith("wallet:"):
            context.user_data["pending_wallet"] = action.split(":", 1)[1]
            await _refresh_preview(query, context)
        elif action == "preview":
            await _refresh_preview(query, context)
        return True
    if data.startswith("budget:"):
        action = data.split(":", 1)[1]
        if action == "choose":
            categories = [key for key, item in CATEGORIES.items() if item["type"] == "out" and key != "lain"]
            rows = [[InlineKeyboardButton(CATEGORIES[key]["name"], callback_data="budget:set:" + key) for key in categories[i:i + 2]] for i in range(0, len(categories), 2)]
            rows.append([InlineKeyboardButton("⬅️ Kembali", callback_data="menu:budget")])
            await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(rows))
        elif action.startswith("set:"):
            category = action.split(":", 1)[1]
            context.user_data["finance_flow"] = "budget:" + category
            await query.edit_message_text(f"Kirim batas bulanan untuk {escape(CATEGORIES.get(category, {}).get('name', category))} dalam Rupiah.", parse_mode=ParseMode.HTML, reply_markup=back_menu())
        elif action == "confirm":
            pending = context.user_data.pop("pending_budget", None)
            if pending:
                apply_bot_mutation(user_id, lambda state: state.setdefault("budgets", {}).__setitem__(pending["category"], pending["amount"]))
                await query.edit_message_text("Batas budget diperbarui.", reply_markup=back_menu())
            else:
                await query.edit_message_text("Perubahan budget tidak ditemukan.", reply_markup=back_menu())
        return True
    if data.startswith("wallet:"):
        action = data.split(":", 1)[1]
        if action == "add":
            context.user_data["finance_flow"] = "wallet:add"
            await query.edit_message_text("Kirim nama dompet baru.", reply_markup=back_menu())
        elif action.startswith("edit:"):
            wallet_id = action.split(":", 1)[1]
            context.user_data["wallet_id"] = wallet_id
            rows = [[InlineKeyboardButton("Ubah nama", callback_data="wallet:rename"), InlineKeyboardButton("Atur saldo awal", callback_data="wallet:opening")], [InlineKeyboardButton("⬅️ Kembali", callback_data="menu:wallet")]]
            await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(rows))
        elif action in {"rename", "opening"}:
            context.user_data["finance_flow"] = "wallet:" + action
            await query.edit_message_text("Kirim nilai baru.", reply_markup=back_menu())
        elif action == "confirm":
            pending = context.user_data.pop("pending_wallet_edit", None)
            if pending:
                def save_wallet(state):
                    for wallet in state.get("wallets", []):
                        if wallet.get("id") == pending["id"]:
                            wallet["n" if pending["kind"] == "rename" else "start"] = pending["value"]
                apply_bot_mutation(user_id, save_wallet)
                await query.edit_message_text("Perubahan dompet tersimpan.", reply_markup=back_menu())
            else:
                await query.edit_message_text("Perubahan dompet tidak ditemukan.", reply_markup=back_menu())
        return True
    if data.startswith("transfer:"):
        parts = data.split(":")
        if parts[1] == "from":
            context.user_data["transfer_from"] = parts[2]
            state = read_bot_state(user_id)
            rows = [[InlineKeyboardButton(wallet["n"][:30], callback_data="transfer:to:" + wallet["id"])] for wallet in state.get("wallets", []) if wallet["id"] != parts[2]]
            rows.append([InlineKeyboardButton("⬅️ Kembali", callback_data="menu:transfer")])
            await query.edit_message_text("Pilih dompet tujuan.", reply_markup=InlineKeyboardMarkup(rows))
        elif parts[1] == "to":
            context.user_data["transfer_to"] = parts[2]
            context.user_data["finance_flow"] = "transfer:amount"
            await query.edit_message_text("Kirim nominal transfer dalam Rupiah.", reply_markup=back_menu())
        elif parts[1] == "confirm":
            amount = int(context.user_data.pop("transfer_amount", 0))
            source, target = context.user_data.pop("transfer_from", ""), context.user_data.pop("transfer_to", "")
            if amount > 0 and source != target:
                apply_bot_mutation(user_id, lambda state: _transfer(state, source, target, amount))
                context.user_data.pop("finance_flow", None)
                await query.edit_message_text(f"Transfer {_money(amount)} berhasil dicatat.", reply_markup=back_menu())
        return True
    if data.startswith("delete:"):
        _, transaction_id = data.split(":", 1)
        tx = next((item for item in read_bot_state(user_id).get("txs", []) if item.get("id") == transaction_id), None)
        if not tx:
            await query.edit_message_text("Transaksi tidak ditemukan.", reply_markup=back_menu())
            return True
        await query.edit_message_text(f"Hapus transaksi {escape(tx.get('desc', 'Transaksi'))} sebesar {_money(tx.get('amt', 0))}?", parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Ya, hapus", callback_data="delete-confirm:" + transaction_id), InlineKeyboardButton("Batal", callback_data="menu:history")]]))
        return True
    if data.startswith("delete-confirm:"):
        transaction_id = data.split(":", 1)[1]
        removed: dict = {}
        def remove(state):
            index = next((i for i, tx in enumerate(state["txs"]) if tx.get("id") == transaction_id), -1)
            if index < 0: return None
            removed["tx"] = state["txs"].pop(index)
            removed["index"] = index
        apply_bot_mutation(user_id, remove)
        context.user_data["undo_transaction"] = removed
        await query.edit_message_text("Transaksi dihapus.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩ Urungkan", callback_data="delete-undo")], [InlineKeyboardButton("⬅️ Kembali", callback_data="menu:history")]]))
        return True
    if data == "delete-undo":
        undo = context.user_data.pop("undo_transaction", None)
        if undo and undo.get("tx"):
            def restore(state): state["txs"].insert(min(undo["index"], len(state["txs"])), undo["tx"])
            apply_bot_mutation(user_id, restore)
        await query.edit_message_text("Penghapusan diurungkan.", reply_markup=back_menu())
        return True
    if data.startswith("reminder:"):
        _, action, reminder_id = data.split(":", 2)
        if action == "paid":
            await query.edit_message_text("Tandai tagihan ini sudah dibayar dan catat sebagai pengeluaran?", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Ya, tandai dibayar", callback_data="reminder:paid-confirm:" + reminder_id), InlineKeyboardButton("Batal", callback_data="menu:reminders")]]))
            return True
        if action == "paid-confirm":
            action = "paid"
        def update_reminder(state):
            reminder = next((item for item in state.get("reminders", []) if item.get("id") == reminder_id), None)
            if not reminder: return
            if action == "paid":
                today = _today(state)
                if reminder.get("last_paid") == today:
                    return
                reminder["last_paid"] = today
                reminder["last_sent"] = today
                reminder["snooze_until"] = None
                state.setdefault("txs", []).append({"id": "b" + uuid.uuid4().hex[:16], "date": today, "desc": reminder["name"], "cat": "tagihan", "amt": int(reminder["amount"]), "w": state.get("wallets", [{}])[0].get("id", "main"), "type": "out"})
            elif action == "snooze": reminder["snooze_until"] = (datetime.now(_zone(state)).date() + timedelta(days=1)).isoformat()
        apply_bot_mutation(user_id, update_reminder)
        await show_reminders(update, context, edit=True)
        return True
    return False


def _save_pending(state: dict, parsed: dict, wallet_id: str | None) -> str:
    if wallet_id not in [wallet.get("id") for wallet in state.get("wallets", [])]:
        wallet_id = state.get("wallets", [{}])[0].get("id", "main")
    transaction_id = "b" + uuid.uuid4().hex[:16]
    state.setdefault("txs", []).append({"id": transaction_id, "date": _today(state), "desc": parsed["description"], "cat": parsed["category"], "amt": parsed["amount"], "w": wallet_id, "type": parsed["type"]})
    return transaction_id


async def _refresh_preview(query, context) -> None:
    parsed = context.user_data.get("pending_transaction")
    if not parsed:
        await query.edit_message_text("Pratinjau sudah tidak tersedia.", reply_markup=back_menu())
        return
    state = read_bot_state(query.from_user.id)
    wallet_id = context.user_data.get("pending_wallet")
    wallet = next((item for item in state.get("wallets", []) if item.get("id") == wallet_id), state.get("wallets", [{}])[0])
    await query.edit_message_text(f"<b>Pratinjau transaksi</b>\n{escape(parsed['description'])}\n{escape(CATEGORIES[parsed['category']]['name'])} · {escape(wallet.get('n', 'Dompet'))}\n{'+' if parsed['type'] == 'in' else '−'}{_money(parsed['amount'])}", parse_mode=ParseMode.HTML, reply_markup=_transaction_keyboard(parsed, state))


def _transfer(state: dict, source: str, target: str, amount: int) -> None:
    wallets = {wallet["id"] for wallet in state.get("wallets", [])}
    if source not in wallets or target not in wallets or source == target or amount <= 0:
        raise ValueError("Transfer tidak valid")
    transfer_id = "x" + uuid.uuid4().hex[:12]
    today = _today(state)
    state["txs"].extend([
        {"id": transfer_id + "o", "date": today, "desc": "Transfer ke " + _wallet_name(state, target), "cat": "lain", "amt": amount, "w": source, "type": "out", "sv": True, "transfer": transfer_id},
        {"id": transfer_id + "i", "date": today, "desc": "Transfer dari " + _wallet_name(state, source), "cat": "lain_in", "amt": amount, "w": target, "type": "in", "sv": True, "transfer": transfer_id},
    ])


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    flow = context.user_data.get("finance_flow")
    text = (update.effective_message.text or "")[:300].strip()
    user_id = _user_id(update)
    if flow == "record":
        await show_record(update, context, text)
        return True
    if flow == "simulation":
        is_loan = text.lower().startswith("cicilan")
        numbers = re.findall(r"\d+(?:[.,]\d+)?", text)
        if len(numbers) < 3:
            await update.effective_message.reply_text("Format belum sesuai. Tabungan: 1000000 8 10. Cicilan: cicilan 100000000 9 10.")
            return True
        principal_or_monthly, annual_rate, years = float(numbers[0].replace(",", ".")), float(numbers[1].replace(",", ".")), float(numbers[2].replace(",", "."))
        if not (0 < principal_or_monthly <= 10_000_000_000_000 and 0 <= annual_rate <= 100 and 0 < years <= 80):
            await update.effective_message.reply_text("Nilai di luar batas yang didukung.")
            return True
        months = int(years * 12)
        rate = annual_rate / 100 / 12
        context.user_data.pop("finance_flow", None)
        if is_loan:
            installment = principal_or_monthly / months if rate == 0 else principal_or_monthly * rate / (1 - (1 + rate) ** -months)
            text = f"<b>Simulasi cicilan anuitas</b>\nPokok: {_money(round(principal_or_monthly))}\nCicilan: {_money(round(installment))}/bulan\nTotal pembayaran: {_money(round(installment * months))}\nAsumsi: bunga tetap {annual_rate:g}% per tahun, tenor {years:g} tahun, pembayaran bulanan; biaya administrasi, asuransi, dan pajak belum dihitung."
        else:
            total = principal_or_monthly * months if rate == 0 else principal_or_monthly * (((1 + rate) ** months - 1) / rate)
            invested = principal_or_monthly * months
            text = f"<b>Simulasi tabungan berkala</b>\nSetoran: {_money(round(principal_or_monthly))}/bulan\nModal disetor: {_money(round(invested))}\nEstimasi nilai akhir: {_money(round(total))}\nAsumsi: setoran akhir bulan, imbal hasil tetap {annual_rate:g}% per tahun, bunga majemuk bulanan; biaya dan pajak belum dihitung. Ini simulasi, bukan jaminan hasil."
        await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=back_menu())
        return True
    if flow and flow.startswith("budget:"):
        parsed = parse_amount(text)
        if not parsed or parsed["value"] < 0:
            await update.effective_message.reply_text("Kirim nominal batas bulanan yang valid.")
            return True
        category = flow.split(":", 1)[1]
        context.user_data["pending_budget"] = {"category": category, "amount": parsed["value"]}
        context.user_data.pop("finance_flow", None)
        await update.effective_message.reply_text(f"Konfirmasi batas {escape(CATEGORIES.get(category, {}).get('name', category))}: {_money(parsed['value'])} per bulan?", parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Konfirmasi", callback_data="budget:confirm"), InlineKeyboardButton("Batal", callback_data="menu:budget")]]))
        return True
    if flow == "transfer:amount":
        parsed = parse_amount(text)
        if not parsed or parsed["value"] <= 0:
            await update.effective_message.reply_text("Kirim nominal transfer yang valid.")
            return True
        context.user_data["transfer_amount"] = parsed["value"]
        state = read_bot_state(user_id)
        source, target = context.user_data.get("transfer_from"), context.user_data.get("transfer_to")
        await update.effective_message.reply_text(f"Konfirmasi transfer {_money(parsed['value'])} dari {escape(_wallet_name(state, source))} ke {escape(_wallet_name(state, target))}?", parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Konfirmasi", callback_data="transfer:confirm"), InlineKeyboardButton("Batal", callback_data="menu:transfer")]]))
        return True
    if flow == "wallet:add":
        name = text[:40]
        def add_wallet(state):
            state.setdefault("wallets", []).append({"id": "w" + uuid.uuid4().hex[:10], "n": name, "t": "Dompet", "c": "#4FE3E6", "i": "wallet", "start": 0})
        apply_bot_mutation(user_id, add_wallet)
        context.user_data.pop("finance_flow", None)
        await update.effective_message.reply_text("Dompet ditambahkan.", reply_markup=back_menu())
        return True
    if flow == "wallet:rename":
        wallet_id, name = context.user_data.get("wallet_id"), text[:40]
        context.user_data["pending_wallet_edit"] = {"id": wallet_id, "kind": "rename", "value": name}
        context.user_data.pop("finance_flow", None)
        await update.effective_message.reply_text(f"Ubah nama dompet menjadi {escape(name)}?", parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Konfirmasi", callback_data="wallet:confirm"), InlineKeyboardButton("Batal", callback_data="menu:wallet")]]))
        return True
    if flow == "wallet:opening":
        parsed = parse_amount(text)
        if not parsed or parsed["value"] < 0:
            await update.effective_message.reply_text("Kirim saldo awal yang valid.")
            return True
        wallet_id = context.user_data.get("wallet_id")
        context.user_data["pending_wallet_edit"] = {"id": wallet_id, "kind": "opening", "value": parsed["value"]}
        context.user_data.pop("finance_flow", None)
        await update.effective_message.reply_text(f"Atur saldo awal menjadi {_money(parsed['value'])}?", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Konfirmasi", callback_data="wallet:confirm"), InlineKeyboardButton("Batal", callback_data="menu:wallet")]]))
        return True
    if flow == "reminder:add":
        match = re.fullmatch(r"(.{1,60})\s+(\d{1,12})\s+(\d{1,2})\s+(harian|mingguan|bulanan)", text, re.I)
        if not match or (match.group(4).lower() == "bulanan" and not 1 <= int(match.group(3)) <= 28) or (match.group(4).lower() == "mingguan" and not 1 <= int(match.group(3)) <= 7):
            await update.effective_message.reply_text("Format: nama tagihan nominal tanggal harian|mingguan|bulanan. Contoh: PLN 420000 5 bulanan")
            return True
        name, amount, day, frequency = match.groups()
        reminder = {"id": "r" + uuid.uuid4().hex[:10], "name": name[:60], "amount": int(amount), "day": int(day), "frequency": frequency.lower(), "active": True, "snooze_until": None, "last_paid": None}
        apply_bot_mutation(user_id, lambda state: state.setdefault("reminders", []).append(reminder))
        context.user_data.pop("finance_flow", None)
        await update.effective_message.reply_text("Pengingat tagihan tersimpan.", reply_markup=back_menu())
        return True
    if flow == "portfolio:add":
        match = re.fullmatch(r"([A-Za-z0-9._-]{1,20})\s+(\d+(?:[.,]\d+)?)\s+(\d+(?:[.,]\d+)?)", text)
        if not match:
            await update.effective_message.reply_text("Format: kode jumlah harga_beli. Contoh: BTC 0,02 60000")
            return True
        code, quantity, purchase_price = match.groups()
        asset = {"id": "p" + uuid.uuid4().hex[:10], "code": code.upper(), "quantity": float(quantity.replace(",", ".")), "purchase_price": float(purchase_price.replace(",", ".")), "currency": "USD" if code.upper() in {"BTC", "ETH"} else "IDR"}
        apply_bot_mutation(user_id, lambda state: state.setdefault("portfolio", []).append(asset))
        context.user_data.pop("finance_flow", None)
        await update.effective_message.reply_text("Aset portofolio dicatat. Nilai terkini akan dihitung saat sumber harga tersedia.", reply_markup=back_menu())
        return True
    return False