import pandas as pd

import data.market as market


def test_get_history_returns_dataframe(monkeypatch):
    fake = pd.DataFrame({"Close": [1, 2, 3], "Volume": [10, 20, 30]})

    class FakeTicker:
        def __init__(self, symbol):
            self.symbol = symbol

        def history(self, period, interval):
            return fake

    monkeypatch.setattr(market.yf, "Ticker", FakeTicker)
    out = market.get_history("AAPL")
    assert list(out["Close"]) == [1, 2, 3]


def test_get_history_empty_on_error(monkeypatch):
    class BoomTicker:
        def __init__(self, symbol):
            pass

        def history(self, period, interval):
            raise RuntimeError("network down")

    monkeypatch.setattr(market.yf, "Ticker", BoomTicker)
    out = market.get_history("AAPL")
    assert out.empty


def test_batch_download_splits_by_ticker(monkeypatch):
    import pandas as pd
    cols = pd.MultiIndex.from_product([["AAPL", "MSFT"], ["Close", "Volume"]])
    data = pd.DataFrame([[1, 10, 2, 20], [3, 30, 4, 40]], columns=cols)
    monkeypatch.setattr(market.yf, "download", lambda *a, **k: data)
    out = market.batch_download(["AAPL", "MSFT"])
    assert set(out) == {"AAPL", "MSFT"}
    assert list(out["AAPL"]["Close"]) == [1, 3]


def test_batch_download_single_ticker(monkeypatch):
    data = pd.DataFrame({"Close": [10, 20], "Volume": [100, 200]})
    monkeypatch.setattr(market.yf, "download", lambda *a, **k: data)
    out = market.batch_download(["AAPL"])
    assert set(out) == {"AAPL"}
    assert list(out["AAPL"]["Close"]) == [10, 20]


def test_batch_download_empty_on_error(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("net")
    monkeypatch.setattr(market.yf, "download", boom)
    assert market.batch_download(["AAPL"]) == {}


def test_get_return_since(monkeypatch):
    fake = pd.DataFrame({"Close": [100.0, 105.0, 110.0]})

    class FakeTicker:
        def __init__(self, symbol):
            pass
        def history(self, start=None):
            return fake

    monkeypatch.setattr(market.yf, "Ticker", FakeTicker)
    ret = market.get_return_since("AAPL", "2026-06-01")
    assert round(ret, 2) == 10.0


def test_get_return_since_none_on_error(monkeypatch):
    class Boom:
        def __init__(self, s):
            pass
        def history(self, start=None):
            raise RuntimeError("net")

    monkeypatch.setattr(market.yf, "Ticker", Boom)
    assert market.get_return_since("AAPL", "2026-06-01") is None


def test_get_spy_return(monkeypatch):
    fake = pd.DataFrame({"Close": [400.0, 410.0]})

    class FakeTicker:
        def __init__(self, symbol):
            pass
        def history(self, start=None):
            return fake

    monkeypatch.setattr(market.yf, "Ticker", FakeTicker)
    assert round(market.get_spy_return("2026-06-01"), 2) == 2.5
