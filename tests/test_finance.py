from bot.services.finance import dashboard_score, empty_state, parse_amount, parse_transaction, prepare_legacy_state
from bot.utils.formatting import signed_rupiah


def test_parser_amount_formats():
    cases = {
        "25.000": 25_000,
        "25rb": 25_000,
        "25k": 25_000,
        "1,5jt": 1_500_000,
        "1.250.000": 1_250_000,
    }
    for text, amount in cases.items():
        result = parse_amount(text)
        assert result and result["value"] == amount
    assert parse_amount("teks tanpa angka") is None


def test_parse_transaction_detects_type_category_and_description():
    assert parse_transaction("kopi 25.000") == {"amount": 25_000, "type": "out", "category": "makan", "description": "kopi"}
    result = parse_transaction("beli buku 1,5jt")
    assert result and result["amount"] == 1_500_000 and result["category"] == "pendidikan"
    assert parse_transaction("gaji 9jt")["type"] == "in"
    assert parse_transaction("teks tanpa angka") is None


def test_import_excludes_seed_data_but_preserves_real_transactions():
    old_state = {
        "txs": [{"id": "s1", "date": "2026-10-01", "amt": 1000, "type": "out"}, {"id": "u1", "date": "2026-10-02", "amt": 2000, "type": "out"}],
        "wallets": [{"id": "main", "n": "Rekening Utama", "start": 6_000_000}],
        "budgets": {"makan": 1_800_000},
        "goals": [{"id": "g1", "n": "Liburan Bali", "t": 12_000_000, "s": 4_500_000}],
        "name": "Sobat",
    }
    prepared, skipped = prepare_legacy_state(old_state)
    assert [tx["id"] for tx in prepared["txs"]] == ["u1"]
    assert prepared["wallets"][0]["start"] == 0
    assert prepared["budgets"] == {} and prepared["goals"] == []
    assert skipped == 4


def test_report_calculation_and_score():
    state = empty_state()
    state["budgets"] = {"makan": 1_000_000}
    state["wallets"][-1]["start"] = 12_000_000
    state["txs"] = [
        {"id": "i1", "date": "2026-09-01", "type": "out", "amt": 2_000_000, "cat": "makan", "w": "main"},
        {"id": "i2", "date": "2026-10-02", "type": "in", "amt": 10_000_000, "cat": "gaji", "w": "main"},
        {"id": "i3", "date": "2026-10-03", "type": "out", "amt": 1_000_000, "cat": "makan", "w": "main"},
        {"id": "i4", "date": "2026-10-03", "type": "in", "amt": 3_000_000, "cat": "lain_in", "w": "ewallet", "sv": True},
    ]
    report = dashboard_score(state, "2026-10")
    assert report["income"] == 10_000_000
    assert report["expenses"] == 1_000_000
    assert report["net"] == 9_000_000
    assert report["budget_used"] == 1
    assert report["score"] == 100
    assert report["top_categories"] == [("makan", 1_000_000)]


def test_empty_month_has_no_score():
    assert dashboard_score(empty_state(), "2026-10")["score"] is None


def test_signed_rupiah_keeps_balance_direction():
    assert signed_rupiah(1_250_000) == "+Rp 1.250.000"
    assert signed_rupiah(-1_250_000) == "−Rp 1.250.000"