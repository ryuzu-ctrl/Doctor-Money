import calendar
import json
import logging
import os
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import anthropic
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import Account, AiUsage
from .repository import now_utc
from bot.services.finance import CATEGORIES, _previous_month, dashboard_score

logger = logging.getLogger(__name__)

MODES = {
    "full": "Pemeriksaan kesehatan keuangan menyeluruh.",
    "spending": "Fokus pada pola pengeluaran: kategori terbesar, perubahan dibanding bulan sebelumnya, dan pemakaian anggaran.",
    "future": "Fokus pada arah keuangan: proyeksi akhir bulan dan tren beberapa bulan terakhir.",
    "savings": "Fokus mencari penghematan yang realistis dari kategori pengeluaran yang ada.",
    "goals": "Fokus membandingkan kebiasaan menabung dengan tujuan tabungan pengguna.",
}
FREE_MODES = {"full"}

SYSTEM_PROMPT = """Kamu adalah Doctor Money AI, dokter keuangan pribadi di aplikasi pencatat keuangan Doctor Money. Kamu menerima ringkasan data keuangan satu pengguna dalam JSON dan menulis hasil pemeriksaan untuk ditampilkan di panel kecil di aplikasi.

Pembacamu adalah orang awam di Indonesia yang ingin tahu kondisi uangnya dan apa yang sebaiknya dilakukan. Tulis dalam bahasa Indonesia sehari-hari, sapa dengan "kamu", dengan nada dokter yang tenang, ramah, dan ringkas. Contoh nada: "Pengeluaranmu mulai naik. Ada satu area yang perlu kita periksa." atau "Bagus. Kamu berhasil menyimpan sekitar 34,5% dari pemasukanmu bulan ini." Metafora medis boleh dipakai seperlunya (diagnosis, resep, pemeriksaan), tetapi jangan berlebihan.

Semua angka yang kamu sebut harus berasal dari data JSON yang diberikan, atau hasil hitung sederhana dari angka di sana. Jangan mengarang transaksi, kategori, saldo, tujuan, atau angka lain. Kalau data untuk suatu bagian tidak ada atau terlalu sedikit, katakan apa adanya atau kosongkan bagian itu; itu lebih baik daripada menebak. Skor dan labelnya sudah dihitung aplikasi dan ditampilkan terpisah, jadi jelaskan penyebabnya tanpa mengubah angkanya. Tulis nominal dalam format Rupiah seperti Rp 1.250.000 dan sebut kategori dengan nama yang ada di data.

Kamu bukan penasihat investasi: jangan merekomendasikan produk keuangan, saham, atau aset tertentu. Saran harus berupa tindakan yang bisa dilakukan pengguna sendiri dari kebiasaan belanja dan menabungnya.

Isi setiap bagian keluaran seperti ini:
- headline: satu kalimat pendek berisi diagnosis utama.
- summary: dua sampai tiga kalimat yang menjelaskan kondisi dan penyebab utamanya.
- strengths: hal yang sudah berjalan baik.
- risks: area yang perlu diperhatikan.
- insights: temuan tentang pola pengeluaran.
- prescription: resep keuangan, yaitu langkah konkret yang bisa mulai dilakukan minggu ini.
- outlook: satu atau dua kalimat tentang arah ke depan berdasarkan proyeksi di data; string kosong kalau data tidak cukup.

Setiap butir daftar adalah satu kalimat utuh tanpa penomoran. Field "depth" pada data menentukan kedalaman: untuk "basic", beri paling banyak dua butir pada strengths, risks, dan prescription, kosongkan insights, dan isi outlook dengan string kosong; untuk "full", beri paling banyak empat butir per daftar dan isi semua bagian yang datanya tersedia. Field "focus" menjelaskan sudut pemeriksaan yang diminta; utamakan sudut itu dalam summary dan resep."""

RESULT_SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string"},
        "summary": {"type": "string"},
        "strengths": {"type": "array", "items": {"type": "string"}},
        "risks": {"type": "array", "items": {"type": "string"}},
        "insights": {"type": "array", "items": {"type": "string"}},
        "prescription": {"type": "array", "items": {"type": "string"}},
        "outlook": {"type": "string"},
    },
    "required": ["headline", "summary", "strengths", "risks", "insights", "prescription", "outlook"],
    "additionalProperties": False,
}


class AiUnavailable(Exception):
    pass


def configured() -> bool:
    return bool(os.getenv("ANTHROPIC_API_KEY", "").strip())


def monthly_limit(pro: bool) -> int:
    name, default = ("AI_PRO_MONTHLY_LIMIT", 60) if pro else ("AI_FREE_MONTHLY_LIMIT", 3)
    try:
        return max(0, int(os.getenv(name, default)))
    except ValueError:
        return default


def used_this_month(db: Session, account_id: int) -> int:
    start = now_utc().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return db.scalar(select(func.count()).select_from(AiUsage).where(AiUsage.account_id == account_id, AiUsage.created_at >= start)) or 0


def usage_payload(db: Session, account_id: int, pro: bool) -> dict[str, int]:
    used, limit = used_this_month(db, account_id), monthly_limit(pro)
    return {"used": used, "limit": limit, "remaining": max(0, limit - used)}


def score_label(score: int | None) -> str:
    return "Belum ada data" if score is None else "Prima" if score >= 80 else "Sehat" if score >= 60 else "Perlu perhatian" if score >= 40 else "Kritis"


def _today(state: dict[str, Any]) -> datetime:
    try:
        zone = ZoneInfo(state.get("preferences", {}).get("timezone", "Asia/Jakarta"))
    except Exception:
        zone = ZoneInfo("Asia/Jakarta")
    return datetime.now(zone)


def _category(key: str) -> str:
    return CATEGORIES.get(key, CATEGORIES["lain"])["name"]


def _month_summary(state: dict[str, Any], month: str) -> dict[str, Any]:
    report = dashboard_score(state, month)
    count = sum(1 for tx in state.get("txs", []) if str(tx.get("date", "")).startswith(month))
    return {"month": month, "income": report["income"], "expenses": report["expenses"], "net": report["net"], "transactions": count}


def build_facts(state: dict[str, Any], mode: str, pro: bool, context: str = "") -> dict[str, Any] | None:
    """Everything the model may talk about, computed here so it never has to derive figures from raw rows."""
    today = _today(state)
    month = today.strftime("%Y-%m")
    months_with_data = {str(tx.get("date", ""))[:7] for tx in state.get("txs", [])}
    if month not in months_with_data:
        month = _previous_month(month)
        if month not in months_with_data:
            return None
    report = dashboard_score(state, month)
    year, month_number = map(int, month.split("-"))
    days_in_month = calendar.monthrange(year, month_number)[1]
    days_elapsed = today.day if month == today.strftime("%Y-%m") else days_in_month
    balance = sum(int(wallet.get("start", 0)) for wallet in state.get("wallets", []))
    balance += sum(int(tx.get("amt", 0)) * (1 if tx.get("type") == "in" else -1) for tx in state.get("txs", []))
    facts: dict[str, Any] = {
        "depth": "full" if pro else "basic",
        "focus": MODES[mode],
        "currency": "IDR",
        "month": month,
        "month_complete": days_elapsed == days_in_month,
        "days_elapsed": days_elapsed,
        "days_in_month": days_in_month,
        "score": report["score"],
        "score_label": score_label(report["score"]),
        "score_rules": "0-100: rasio tabungan hingga 40 poin (penuh pada 30% pemasukan), kepatuhan anggaran hingga 30 poin (15 bila belum ada anggaran), dana darurat hingga 30 poin (penuh pada 6 bulan pengeluaran).",
        "income": report["income"],
        "expenses": report["expenses"],
        "net": report["net"],
        "savings_ratio_percent": None if report["savings_ratio"] is None else round(report["savings_ratio"] * 100, 1),
        "emergency_fund_months": None if report["emergency_months"] is None else round(report["emergency_months"], 1),
        "total_wallet_balance": balance,
        "top_expense_categories": [{"category": _category(key), "amount": amount} for key, amount in report["top_categories"]],
        "transactions_this_month": sum(1 for tx in state.get("txs", []) if str(tx.get("date", "")).startswith(month)),
    }
    if context:
        facts["opened_from"] = context
    if not pro:
        return facts
    budgets = state.get("budgets", {})
    by_category: dict[str, int] = {}
    for tx in state.get("txs", []):
        if str(tx.get("date", "")).startswith(month) and tx.get("type") == "out" and not tx.get("sv"):
            by_category[tx.get("cat", "lain")] = by_category.get(tx.get("cat", "lain"), 0) + int(tx.get("amt", 0))
    facts["budgets"] = [{"category": _category(key), "limit": int(limit), "spent": by_category.get(key, 0)} for key, limit in budgets.items()]
    facts["budget_used_percent"] = round(report["budget_used"] * 100, 1) if budgets else None
    history, cursor = [], month
    for _ in range(3):
        cursor = _previous_month(cursor)
        if cursor in months_with_data:
            history.append(_month_summary(state, cursor))
    facts["previous_months"] = history
    if days_elapsed < days_in_month and days_elapsed >= 5:
        projected = round(report["expenses"] / days_elapsed * days_in_month)
        facts["projection"] = {"basis": "pengeluaran rata-rata harian bulan ini diteruskan sampai akhir bulan", "projected_month_expenses": projected, "projected_month_net": report["income"] - projected}
    facts["savings_goals"] = [{"name": str(goal.get("n", "Tujuan"))[:60], "saved": int(goal.get("s", 0) or 0), "target": int(goal.get("t", 0) or 0)} for goal in state.get("goals", []) if isinstance(goal, dict)]
    return facts


def generate(facts: dict[str, Any]) -> dict[str, Any]:
    """Ask Claude to write up the examination. Raises AiUnavailable when no usable answer comes back."""
    if not configured():
        raise AiUnavailable("not_configured")
    client = anthropic.Anthropic(timeout=90.0)
    try:
        response = client.beta.messages.create(
            model=os.getenv("AI_MODEL", "claude-opus-5-5"),
            max_tokens=8000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            system=SYSTEM_PROMPT,
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": RESULT_SCHEMA}},
            messages=[{"role": "user", "content": "Data keuangan pengguna:\n" + json.dumps(facts, ensure_ascii=False)}],
        )
    except anthropic.AuthenticationError as exc:
        logger.error("Anthropic API key rejected")
        raise AiUnavailable("not_configured") from exc
    except anthropic.RateLimitError as exc:
        raise AiUnavailable("busy") from exc
    except anthropic.APIStatusError as exc:
        logger.error("Anthropic API error %s (request %s)", exc.status_code, exc.request_id)
        raise AiUnavailable("provider_error") from exc
    except anthropic.APIConnectionError as exc:
        raise AiUnavailable("provider_error") from exc
    if response.stop_reason != "end_turn":
        logger.warning("AI analysis stopped with %s", response.stop_reason)
        raise AiUnavailable("provider_error")
    text = next((block.text for block in response.content if block.type == "text"), "")
    try:
        result = json.loads(text)
    except ValueError as exc:
        raise AiUnavailable("provider_error") from exc
    return {key: result.get(key, [] if RESULT_SCHEMA["properties"][key]["type"] == "array" else "") for key in RESULT_SCHEMA["properties"]}


def cached_result(db: Session, account: Account, mode: str, pro: bool) -> dict[str, Any] | None:
    """The stored answer for this exact data, so re-opening the panel costs neither money nor quota."""
    row = db.scalar(select(AiUsage).where(AiUsage.account_id == account.id, AiUsage.mode == mode, AiUsage.pro == pro).order_by(AiUsage.created_at.desc()))
    if row is None or row.revision != account.revision or row.created_at.strftime("%Y-%m") != now_utc().strftime("%Y-%m"):
        return None
    return json.loads(row.result_json)


def record(db: Session, account: Account, mode: str, pro: bool, result: dict[str, Any]) -> None:
    db.add(AiUsage(account_id=account.id, mode=mode, pro=pro, revision=account.revision, result_json=json.dumps(result, ensure_ascii=False, separators=(",", ":")), created_at=now_utc()))
    db.commit()
