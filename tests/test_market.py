import asyncio

import pytest

from bot.services import market


def test_yahoo_market_data_maps_stock_and_commodity_quotes(monkeypatch):
    batches = []

    async def yahoo_response(url, params, ttl):
        assert url.endswith("/v7/finance/spark")
        assert params["range"] == "5d"
        batches.append(params["symbols"].split(","))
        assert len(batches[-1]) <= 20
        return {
            "spark": {
                "result": [
                    {"symbol": "BBCA.JK", "response": [{"meta": {"currency": "IDR", "regularMarketPrice": 10_500, "regularMarketChangePercent": 2.5, "regularMarketVolume": 100, "regularMarketTime": 123, "chartPreviousClose": 10_000}}]},
                    {"symbol": "GC=F", "response": [{"meta": {"currency": "USD", "regularMarketPrice": 2_100, "regularMarketChangePercent": 5, "regularMarketTime": 456, "chartPreviousClose": 2_000}}]},
                ]
            }
        }

    monkeypatch.setattr(market, "_get", yahoo_response)
    result = asyncio.run(market.yahoo_market_data())

    assert len(batches) == 2
    assert result["stocks"] == [{
        "symbol": "BBCA.JK",
        "name": "Bank Central Asia",
        "currency": "IDR",
        "unit": "per saham",
        "price": 10_500,
        "change_pct": 2.5,
        "volume": 100,
    }]
    assert result["commodities"][0]["symbol"] == "GC=F"
    assert result["commodities"][0]["price"] == 2_100
    assert result["commodities"][0]["change_pct"] == 5
    assert result["stocks_as_of"] == 123
    assert result["commodities_as_of"] == 456


def test_yahoo_market_data_returns_top_five_gaining_stocks(monkeypatch):
    universe = market.YAHOO_MARKET_ITEMS["stocks"][:8]

    async def yahoo_response(url, params, ttl):
        return {
            "spark": {
                "result": [
                    {
                        "symbol": item["symbol"],
                        "response": [{"meta": {
                            "regularMarketPrice": 100,
                            "regularMarketChangePercent": index - 2,
                            "regularMarketVolume": index,
                        }}],
                    }
                    for index, item in enumerate(universe)
                ]
            }
        }

    monkeypatch.setattr(market, "_get", yahoo_response)
    result = asyncio.run(market.yahoo_market_data())

    assert [quote["symbol"] for quote in result["stocks"]] == [item["symbol"] for item in reversed(universe[3:])]
    assert all(quote["change_pct"] > 0 for quote in result["stocks"])
    assert result["stocks_monitored"] == len(market.YAHOO_MARKET_ITEMS["stocks"])


def test_yahoo_market_data_uses_closes_when_market_price_is_missing(monkeypatch):
    async def yahoo_response(url, params, ttl):
        return {
            "spark": {
                "result": [
                    {
                        "symbol": "BBCA.JK",
                        "response": [{
                            "meta": {"currency": "IDR"},
                            "indicators": {"quote": [{"close": [9_000, 10_000]}]},
                        }],
                    }
                ]
            }
        }

    monkeypatch.setattr(market, "_get", yahoo_response)
    result = asyncio.run(market.yahoo_market_data())

    assert result["stocks"][0]["price"] == 10_000
    assert result["stocks"][0]["change_pct"] == pytest.approx((10_000 - 9_000) / 9_000 * 100)


def test_yahoo_market_data_reports_empty_provider_response(monkeypatch):
    async def yahoo_response(url, params, ttl):
        return {"spark": {"result": []}}

    monkeypatch.setattr(market, "_get", yahoo_response)
    with pytest.raises(RuntimeError, match="tidak tersedia"):
        asyncio.run(market.yahoo_market_data())
