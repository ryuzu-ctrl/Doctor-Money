import asyncio
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