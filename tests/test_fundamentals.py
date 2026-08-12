import data.fundamentals as fundamentals


def test_get_fundamentals_maps_fields(monkeypatch):
    class FakeTicker:
        def __init__(self, symbol):
            self.info = {"trailingPE": 30.0, "marketCap": 1_000, "revenueGrowth": 0.08}

    monkeypatch.setattr(fundamentals.yf, "Ticker", FakeTicker)
    out = fundamentals.get_fundamentals("AAPL")
    assert out["pe"] == 30.0
    assert out["market_cap"] == 1_000
    assert out["revenue_growth"] == 0.08
    assert out["forward_pe"] is None
    assert out["profit_margin"] is None
    assert out["sector"] is None


def test_get_fundamentals_empty_on_error(monkeypatch):
    class BoomTicker:
        def __init__(self, symbol):
            raise RuntimeError("no info")

    monkeypatch.setattr(fundamentals.yf, "Ticker", BoomTicker)
    assert fundamentals.get_fundamentals("AAPL") == {}


def test_get_fundamentals_richer_fields(monkeypatch):
    class FakeTicker:
        def __init__(self, s):
            self.info = {"trailingPE": 30.0, "forwardPE": 25.0, "marketCap": 1000,
                         "revenueGrowth": 0.08, "profitMargins": 0.21, "sector": "Technology"}
    monkeypatch.setattr(fundamentals.yf, "Ticker", FakeTicker)
    out = fundamentals.get_fundamentals("AAPL")
    assert out["forward_pe"] == 25.0
    assert out["profit_margin"] == 0.21
    assert out["sector"] == "Technology"


def test_get_sector(monkeypatch):
    class FakeTicker:
        def __init__(self, s):
            self.info = {"sector": "Energy"}
    monkeypatch.setattr(fundamentals.yf, "Ticker", FakeTicker)
    fundamentals.get_sector.cache_clear()
    assert fundamentals.get_sector("XOM") == "Energy"
