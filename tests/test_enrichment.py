import data.enrichment as enrichment


def test_enrich_assembles_candidate(monkeypatch):
    monkeypatch.setattr(enrichment, "get_fundamentals",
                        lambda t: {"pe": 30.0, "forward_pe": 25.0, "revenue_growth": 0.08,
                                   "market_cap": 1e9, "profit_margin": 0.2, "sector": "Tech"})
    monkeypatch.setattr(enrichment, "get_news", lambda t, limit=5: ["headline"])
    monkeypatch.setattr(enrichment, "get_stocktwits", lambda t: {"bullish": 5, "bearish": 1})
    monkeypatch.setattr(enrichment, "get_reddit", lambda t: {"mentions": 3})
    ind = {"AAPL": {"price": 200.0, "rsi": 28.0, "macd_hist": 0.4, "above_sma200": True,
                    "golden_cross": False, "death_cross": False, "volume_spike": True}}
    out = enrichment.enrich_candidates(["AAPL"], ind, {"AAPL": 6})
    c = out[0]
    assert c["ticker"] == "AAPL" and c["price"] == 200.0 and c["score"] == 6
    assert c["pe"] == 30.0 and c["sector"] == "Tech"
    assert c["news"] == ["headline"] and c["stocktwits"]["bullish"] == 5


def test_enrich_skips_ticker_without_indicators(monkeypatch):
    monkeypatch.setattr(enrichment, "get_fundamentals", lambda t: {})
    monkeypatch.setattr(enrichment, "get_news", lambda t, limit=5: [])
    monkeypatch.setattr(enrichment, "get_stocktwits", lambda t: {})
    monkeypatch.setattr(enrichment, "get_reddit", lambda t: {})
    assert enrichment.enrich_candidates(["AAPL"], {}, {}) == []
