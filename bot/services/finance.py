import re
import os
from datetime import date
from typing import Any


CATEGORIES: dict[str, dict[str, Any]] = {
    "makan": {"name": "Makan & Minum", "type": "out", "terms": r"kopi|makan|minum|nasi|ayam|bakso|mie|sate|snack|jajan|resto|cafe|kafe|sarapan|lunch|dinner|boba|teh|roti|warteg|bubur|gorengan"},
    "transport": {"name": "Transportasi", "type": "out", "terms": r"bensin|pertamax|pertalite|parkir|tol\b|ojek|grab|gojek|gocar|krl|mrt|lrt|kereta|bus\b|taksi|angkot|transjakarta|servis motor"},
    "belanja": {"name": "Belanja", "type": "out", "terms": r"belanja|beli|baju|sepatu|shopee|tokopedia|lazada|indomaret|alfamart|supermarket|kaos|tas\b|perabot"},
    "tagihan": {"name": "Tagihan", "type": "out", "terms": r"listrik|pln|token|internet|wifi|indihome|pulsa|paket data|pdam|kos\b|kost|sewa|kontrakan|cicilan|angsuran|bpjs|asuransi|pajak|tagihan"},
    "hiburan": {"name": "Hiburan", "type": "out", "terms": r"netflix|spotify|bioskop|nonton|game|steam|youtube|konser|wisata|liburan|karaoke|streaming"},
    "kesehatan": {"name": "Kesehatan", "type": "out", "terms": r"dokter|obat|apotek|klinik|rumah sakit|vitamin|checkup|lab\b|dentist|gigi|terapi"},
    "pendidikan": {"name": "Pendidikan", "type": "out", "terms": r"buku|kursus|kuliah|sekolah|spp|les\b|kelas|seminar|udemy|webinar|ujian"},
    "lain": {"name": "Lainnya", "type": "out", "terms": ""},
    "gaji": {"name": "Gaji", "type": "in", "terms": r"gaji|salary|payroll"},
    "freelance": {"name": "Freelance", "type": "in", "terms": r"freelance|proyek|honor|fee\b|jasa|komisi"},
    "bonus": {"name": "Bonus", "type": "in", "terms": r"bonus|thr\b|cashback|hadiah|refund"},
    "lain_in": {"name": "Lainnya", "type": "in", "terms": ""},
}
OUT_ORDER = ("kesehatan", "pendidikan", "tagihan", "transport", "hiburan", "makan", "belanja")
IN_ORDER = ("gaji", "freelance", "bonus")
IN_RE = re.compile(r"\b(?:gaji|salary|payroll|bonus|thr\b|freelance|honor|dividen|cashback|komisi|pendapatan|terima|refund|hadiah)", re.I)
AMOUNT_RE = re.compile(r"(\d{1,3}(?:[.,]\d{3})+|\d+(?:[.,]\d+)?)\s*(juta|jt|ribu|rb|k)?(?![\w])", re.I)
SEED_WALLETS = {"main": 6_000_000, "ewallet": 4_000_000, "cash": 1_500_000, "sav": 24_000_000}
SEED_BUDGETS = {"makan": 1_800_000, "transport": 600_000, "belanja": 1_200_000, "tagihan": 4_000_000, "hiburan": 400_000, "kesehatan": 300_000, "pendidikan": 300_000}
SEED_GOALS = {
    "g1": {"id": "g1", "n": "Liburan Bali", "t": 12_000_000, "s": 4_500_000},
    "g2": {"id": "g2", "n": "Laptop Baru", "t": 15_000_000, "s": 6_200_000},
    "g3": {"id": "g3", "n": "Uang Muka Rumah", "t": 50_000_000, "s": 8_000_000},
}


def parse_amount(text: str) -> dict[str, int] | None:
    """Parse the last amount in free text using the dashboard's Indonesian rules."""
    matches = list(AMOUNT_RE.finditer(text))
    if not matches:
        return None
    match = matches[-1]
    raw, unit = match.group(1), (match.group(2) or "").lower()
    multiplier = 1_000_000 if unit in {"juta", "jt"} else 1_000 if unit in {"ribu", "rb", "k"} else 1
    grouped = bool(re.fullmatch(r"\d{1,3}([.,]\d{3})+", raw))
    if multiplier > 1:
        if grouped and "." in raw and "," not in raw and len(raw.split(".")[1]) == 3 and float(raw.replace(".", "")) >= 100:
            value = int(raw.replace(".", "")) * multiplier
        else:
            value = float(raw.replace(".", "").replace(",", ".") if grouped and "." in raw else raw.replace(",", ".")) * multiplier
    elif grouped:
        value = int(re.sub(r"[.,]", "", raw))
    else:
        value = float(raw.replace(",", "."))
    return {"value": round(value), "start": match.start(), "end": match.end()}


def parse_transaction(text: str) -> dict[str, Any] | None:
    parsed = parse_amount(text)
    if not parsed or parsed["value"] <= 0:
        return None
    transaction_type = "in" if IN_RE.search(text) else "out"
    order = IN_ORDER if transaction_type == "in" else OUT_ORDER
    category = next((key for key in order if re.search(CATEGORIES[key]["terms"], text, re.I)), "lain_in" if transaction_type == "in" else "lain")
    description = (text[:parsed["start"]] + " " + text[parsed["end"]:]).strip()
    description = re.sub(r"\brp\.?\s*$", "", description, flags=re.I)
    description = re.sub(r"\s+", " ", description).strip(" ·,-")
    return {"amount": parsed["value"], "type": transaction_type, "category": category, "description": description[:120] or CATEGORIES[category]["name"]}


def empty_state(name: str = "Sobat") -> dict[str, Any]:
    return {
        "txs": [],
        "wallets": [
            {"id": "main", "n": "Rekening Utama", "t": "Bank", "c": "#6CB6FF", "i": "wallet2", "start": 0},
            {"id": "ewallet", "n": "E-wallet", "t": "Dompet digital", "c": "#5FE3B4", "i": "wallet", "start": 0},
            {"id": "cash", "n": "Tunai", "t": "Dompet fisik", "c": "#FFB86B", "i": "coin", "start": 0},
            {"id": "sav", "n": "Tabungan Darurat", "t": "Dana darurat", "c": "#C79BFF", "i": "shield", "start": 0},
        ],
        "budgets": {},
        "goals": [],
        "name": name,
        "preferences": {"timezone": os.getenv("TZ", "Asia/Jakarta")},
        "reminders": [],
        "watchlist": [],
        "alerts": [],
        "portfolio": [],
    }


def dashboard_score(state: dict[str, Any], year_month: str) -> dict[str, Any]:
    transactions = [tx for tx in state.get("txs", []) if tx.get("date", "").startswith(year_month)]
    income = sum(int(tx.get("amt", 0)) for tx in transactions if tx.get("type") == "in" and not tx.get("sv"))
    expenses_by_category: dict[str, int] = {}
    for tx in transactions:
        if tx.get("type") == "out" and not tx.get("sv"):
            expenses_by_category[tx.get("cat", "lain")] = expenses_by_category.get(tx.get("cat", "lain"), 0) + int(tx.get("amt", 0))
    expenses = sum(expenses_by_category.values())
    budget_total = sum(int(value) for value in state.get("budgets", {}).values())
    budget_spent = sum(expenses_by_category.get(key, 0) for key in state.get("budgets", {}))
    savings_ratio = (income - expenses) / income if income else None
    budget_used = budget_spent / budget_total if budget_total else 0
    wallets = {wallet["id"]: wallet for wallet in state.get("wallets", [])}
    emergency = wallets.get("sav")
    emergency_balance = int(emergency.get("start", 0)) if emergency else 0
    for tx in state.get("txs", []):
        if tx.get("w") == "sav":
            emergency_balance += int(tx.get("amt", 0)) * (1 if tx.get("type") == "in" else -1)
    prior = [tx for tx in state.get("txs", []) if tx.get("type") == "out" and not tx.get("sv") and tx.get("date", "").startswith(_previous_month(year_month))]
    prior_expense = sum(int(tx.get("amt", 0)) for tx in prior)
    emergency_months = emergency_balance / (prior_expense or expenses) if prior_expense or expenses else None
    if not transactions:
        score = None
    else:
        savings_points = min(1, max(0, (savings_ratio or 0) / 0.3)) * 40
        # Same rule as the dashboard: without any budget set, this part earns half its points.
        budget_points = 15 if not budget_total else 30 if budget_used <= 1 else max(0, 30 * (1 - (budget_used - 1) * 2))
        emergency_points = min(1, max(0, emergency_months or 0) / 6) * 30
        score = round(savings_points + budget_points + emergency_points)
    return {"income": income, "expenses": expenses, "net": income - expenses, "savings_ratio": savings_ratio, "budget_used": budget_used, "emergency_months": emergency_months, "score": score, "top_categories": sorted(expenses_by_category.items(), key=lambda item: item[1], reverse=True)[:5]}


def _previous_month(year_month: str) -> str:
    year, month = map(int, year_month.split("-"))
    return f"{year - (month == 1):04d}-{12 if month == 1 else month - 1:02d}"


def prepare_legacy_state(legacy: dict[str, Any]) -> tuple[dict[str, Any], int]:
    state = empty_state(str(legacy.get("name") or "Sobat")[:40])
    state["txs"] = [tx for tx in legacy.get("txs", []) if isinstance(tx, dict) and not re.fullmatch(r"s\d+", str(tx.get("id", ""))) and int(tx.get("amt", 0) or 0) > 0]
    skipped = len([tx for tx in legacy.get("txs", []) if isinstance(tx, dict)]) - len(state["txs"])
    wallets = []
    for wallet in legacy.get("wallets", []):
        if not isinstance(wallet, dict) or not wallet.get("id"):
            continue
        wallet = dict(wallet)
        if wallet.get("id") in SEED_WALLETS and int(wallet.get("start", 0)) == SEED_WALLETS[wallet["id"]]:
            wallet["start"] = 0
            skipped += 1
        wallets.append(wallet)
    if wallets:
        state["wallets"] = wallets
    budgets = legacy.get("budgets", {})
    state["budgets"] = {key: int(value) for key, value in budgets.items() if int(value) >= 0 and SEED_BUDGETS.get(key) != int(value)}
    skipped += sum(1 for key, value in budgets.items() if SEED_BUDGETS.get(key) == int(value))
    goals = [goal for goal in legacy.get("goals", []) if isinstance(goal, dict)]
    state["goals"] = [goal for goal in goals if SEED_GOALS.get(goal.get("id")) != goal]
    skipped += len(goals) - len(state["goals"])
    state["preferences"] = legacy.get("preferences", state["preferences"])
    return state, skipped


def format_rupiah(amount: int) -> str:
    return "Rp " + f"{abs(int(amount)):,}".replace(",", ".")


def format_date(value: str, timezone: str = "Asia/Jakarta") -> str:
    try:
        parsed = date.fromisoformat(value[:10])
        months = ("Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des")
        return f"{parsed.day} {months[parsed.month - 1]} {parsed.year}"
    except (TypeError, ValueError):
        return value