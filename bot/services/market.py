import asyncio
import math
import os
import time
from typing import Any

import httpx


DISCLAIMER = "Informasi ini bukan saran investasi."
UNAVAILABLE = "Data sedang tidak tersedia, coba lagi sebentar."
_cache: dict[str, tuple[float, Any]] = {}


async def _get(url: str, params: dict[str, Any] | None = None, ttl: int = 45) -> Any:
    key = url + repr(sorted((params or {}).items()))
    cached = _cache.get(key)
    if cached and cached[0] > time.monotonic():
        return cached[1]
    headers = {"User-Agent": "DoctorMoneyBot/1.0"}
    if "coingecko" in url and os.getenv("COINGECKO_API_KEY"):
        headers["x-cg-demo-api-key"] = os.environ["COINGECKO_API_KEY"]
    last_error = None
    for attempt in range(2):
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(8.0), follow_redirects=True) as client:
                response = await client.get(url, params=params, headers=headers)
                response.raise_for_status()
                result = response.json()
                _cache[key] = (time.monotonic() + ttl, result)
                return result
        except (httpx.HTTPError, ValueError) as exc:
            last_error = exc
            if attempt == 0:
                await asyncio.sleep(0.25)
    raise RuntimeError(UNAVAILABLE) from last_error


async def top_crypto() -> list[dict[str, Any]]:
    return await _get("https://api.coingecko.com/api/v3/coins/markets", {"vs_currency": "idr", "order": "market_cap_desc", "per_page": 10, "page": 1, "sparkline": "false"})


YAHOO_MARKET_ITEMS = {
    "stocks": [
        {"symbol": "BBCA.JK", "name": "Bank Central Asia", "currency": "IDR", "unit": "per saham"},
        {"symbol": "BBRI.JK", "name": "Bank Rakyat Indonesia", "currency": "IDR", "unit": "per saham"},
        {"symbol": "BMRI.JK", "name": "Bank Mandiri", "currency": "IDR", "unit": "per saham"},
        {"symbol": "TLKM.JK", "name": "Telkom Indonesia", "currency": "IDR", "unit": "per saham"},
        {"symbol": "ASII.JK", "name": "Astra International", "currency": "IDR", "unit": "per saham"},
        {"symbol": "ANTM.JK", "name": "Aneka Tambang", "currency": "IDR", "unit": "per saham"},
        {"symbol": "BRPT.JK", "name": "Barito Pacific", "currency": "IDR", "unit": "per saham"},
        {"symbol": "MDKA.JK", "name": "Merdeka Copper Gold", "currency": "IDR", "unit": "per saham"},
        {"symbol": "AMMN.JK", "name": "Amman Mineral Internasional", "currency": "IDR", "unit": "per saham"},
        {"symbol": "PTBA.JK", "name": "Bukit Asam", "currency": "IDR", "unit": "per saham"},
        {"symbol": "ADRO.JK", "name": "Alamtri Resources Indonesia", "currency": "IDR", "unit": "per saham"},
        {"symbol": "MEDC.JK", "name": "Medco Energi Internasional", "currency": "IDR", "unit": "per saham"},
        {"symbol": "UNTR.JK", "name": "United Tractors", "currency": "IDR", "unit": "per saham"},
        {"symbol": "BBNI.JK", "name": "Bank Negara Indonesia", "currency": "IDR", "unit": "per saham"},
        {"symbol": "ICBP.JK", "name": "Indofood CBP Sukses Makmur", "currency": "IDR", "unit": "per saham"},
        {"symbol": "BBTN.JK", "name": "Bank Tabungan Negara", "currency": "IDR", "unit": "per saham"},
        {"symbol": "GOTO.JK", "name": "GoTo Gojek Tokopedia", "currency": "IDR", "unit": "per saham"},
        {"symbol": "BREN.JK", "name": "Barito Renewables Energy", "currency": "IDR", "unit": "per saham"},
        {"symbol": "CPIN.JK", "name": "Charoen Pokphand Indonesia", "currency": "IDR", "unit": "per saham"},
        {"symbol": "SMGR.JK", "name": "Semen Indonesia", "currency": "IDR", "unit": "per saham"},
        {"symbol": "EXCL.JK", "name": "XLSMART Telecom Sejahtera", "currency": "IDR", "unit": "per saham"},
        {"symbol": "ISAT.JK", "name": "Indosat", "currency": "IDR", "unit": "per saham"},
        {"symbol": "PGAS.JK", "name": "Perusahaan Gas Negara", "currency": "IDR", "unit": "per saham"},
        {"symbol": "ACES.JK", "name": "Aspirasi Hidup Indonesia", "currency": "IDR", "unit": "per saham"},
        {"symbol": "AMRT.JK", "name": "Sumber Alfaria Trijaya", "currency": "IDR", "unit": "per saham"},
        {"symbol": "BRIS.JK", "name": "Bank Syariah Indonesia", "currency": "IDR", "unit": "per saham"},
        {"symbol": "INCO.JK", "name": "Vale Indonesia", "currency": "IDR", "unit": "per saham"},
        {"symbol": "JPFA.JK", "name": "Japfa Comfeed Indonesia", "currency": "IDR", "unit": "per saham"},
        {"symbol": "MYOR.JK", "name": "Mayora Indah", "currency": "IDR", "unit": "per saham"},
        {"symbol": "KLBF.JK", "name": "Kalbe Farma", "currency": "IDR", "unit": "per saham"},
    ],
    "commodities": [
        {"symbol": "GC=F", "name": "Emas futures", "currency": "USD", "unit": "per troy ounce"},
        {"symbol": "SI=F", "name": "Perak futures", "currency": "USD", "unit": "per troy ounce"},
        {"symbol": "CL=F", "name": "Minyak WTI futures", "currency": "USD", "unit": "per barrel"},
        {"symbol": "NG=F", "name": "Gas alam futures", "currency": "USD", "unit": "per MMBtu"},
    ],
}


async def yahoo_market_data() -> dict[str, Any]:
    items = [item for group in YAHOO_MARKET_ITEMS.values() for item in group]
    by_symbol: dict[str, dict[str, Any]] = {}
    for offset in range(0, len(items), 20):
        batch = items[offset:offset + 20]
        result = await _get(
            "https://query1.finance.yahoo.com/v7/finance/spark",
            {"symbols": ",".join(item["symbol"] for item in batch), "range": "5d", "interval": "1d"},
            ttl=60,
        )
        responses = result.get("spark", {}).get("result")
        if not isinstance(responses, list):
            raise RuntimeError(UNAVAILABLE)
        by_symbol.update({item.get("symbol"): item for item in responses if isinstance(item, dict)})

    quotes: dict[str, list[dict[str, Any]]] = {"stocks": [], "commodities": []}
    stock_times: list[int] = []
    commodity_times: list[int] = []
    for group, group_items in YAHOO_MARKET_ITEMS.items():
        for item in group_items:
            spark = by_symbol.get(item["symbol"], {})
            chart_responses = spark.get("response")
            response = chart_responses[0] if isinstance(chart_responses, list) and chart_responses and isinstance(chart_responses[0], dict) else {}
            meta = response.get("meta")
            meta = meta if isinstance(meta, dict) else {}
            indicators = response.get("indicators")
            quote_data = indicators.get("quote") if isinstance(indicators, dict) else None
            quote = quote_data[0] if isinstance(quote_data, list) and quote_data and isinstance(quote_data[0], dict) else {}
            closes = quote.get("close") or []
            valid_closes = [value for value in closes if isinstance(value, (int, float)) and math.isfinite(value)]
            price = meta.get("regularMarketPrice")
            if not isinstance(price, (int, float)) or not math.isfinite(price):
                price = valid_closes[-1] if valid_closes else None
            previous = meta.get("chartPreviousClose")
            if not isinstance(previous, (int, float)) or not math.isfinite(previous):
                previous = valid_closes[-2] if len(valid_closes) > 1 else None
            if price is None:
                continue
            change = meta.get("regularMarketChangePercent")
            if not isinstance(change, (int, float)) or not math.isfinite(change):
                change = (price - previous) / previous * 100 if previous and previous > 0 else None
            market_time = meta.get("regularMarketTime")
            if isinstance(market_time, (int, float)) and math.isfinite(market_time):
                (stock_times if group == "stocks" else commodity_times).append(int(market_time))
            quotes[group].append({
                **item,
                "price": price,
                "change_pct": change,
                "currency": meta.get("currency") or item["currency"],
                "volume": meta.get("regularMarketVolume"),
            })

    if not any(quotes.values()):
        raise RuntimeError(UNAVAILABLE)
    stocks = sorted(
        (quote for quote in quotes["stocks"] if quote["change_pct"] is not None and quote["change_pct"] > 0),
        key=lambda quote: (quote["change_pct"], quote["volume"] or 0),
        reverse=True,
    )
    return {
        **quotes,
        "stocks": stocks[:5],
        "stocks_monitored": len(YAHOO_MARKET_ITEMS["stocks"]),
        "stocks_as_of": max(stock_times, default=None),
        "commodities_as_of": max(commodity_times, default=None),
    }


async def find_crypto(query: str) -> dict[str, Any]:
    query = query.strip()[:64]
    if not query:
        raise ValueError("Ketik nama atau simbol aset crypto.")
    search = await _get("https://api.coingecko.com/api/v3/search", {"query": query})
    coins = search.get("coins", [])
    coin = next((item for item in coins if item.get("symbol", "").lower() == query.lower()), coins[0] if coins else None)
    if not coin:
        raise ValueError("Aset crypto tidak ditemukan.")
    result = await _get("https://api.coingecko.com/api/v3/coins/markets", {"vs_currency": "idr", "ids": coin["id"], "price_change_percentage": "24h,7d"})
    if not result:
        raise ValueError("Aset crypto tidak ditemukan.")
    return result[0]


async def exchange_rate(base: str = "USD", quote: str = "IDR") -> float:
    result = await _get("https://api.frankfurter.app/latest", {"from": base.upper(), "to": quote.upper()}, ttl=300)
    return float(result["rates"][quote.upper()])


async def fear_greed() -> tuple[dict[str, Any], dict[str, Any] | None]:
    result = await _get("https://api.alternative.me/fng/", {"limit": 2, "format": "json"}, ttl=300)
    values = result.get("data", [])
    if not values:
        raise RuntimeError(UNAVAILABLE)
    return values[0], values[1] if len(values) > 1 else None


async def stock_data(_: str | None = None) -> None:
    raise RuntimeError("Data saham IDX gratis yang legal dan stabil belum dipilih. Opsi: Alpha Vantage (kuota gratis, API key), Twelve Data (kuota terbatas/berbayar), atau lisensi data resmi BEI.")


async def gold_data() -> None:
    raise RuntimeError("Sumber harga emas gram IDR belum dipilih. Opsi: API harga emas berlisensi (umumnya berbayar), atau input harga manual dengan sumber dan waktu pembaruan yang dicatat.")