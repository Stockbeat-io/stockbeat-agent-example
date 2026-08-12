import pandas as pd

from data.screener import apply_sector_cap, build_indicators, score_ticker, select_candidates


def test_score_bullish_signals():
    ind = {"rsi": 25.0, "macd_hist": 0.5, "volume_spike": True,
           "above_sma200": True, "golden_cross": True, "death_cross": False}
    # +2 (rsi) +2 (macd) +1 (vol) +1 (sma200) +3 (golden) = 9
    assert score_ticker(ind) == 9


def test_score_bearish_signals():
    ind = {"rsi": 80.0, "macd_hist": -0.5, "volume_spike": False,
           "above_sma200": False, "golden_cross": False, "death_cross": True}
    # -2 (rsi) -2 (macd) -3 (death) = -7
    assert score_ticker(ind) == -7


def test_score_handles_nan_rsi():
    ind = {"rsi": float("nan"), "macd_hist": float("nan")}
    assert score_ticker(ind) == 0


def test_select_includes_holdings_and_extremes():
    scores = {"A": 9, "B": 5, "C": -7, "D": 0, "HELD": -1}
    out = select_candidates(scores, holdings=["HELD"], top_bull=2, top_bear=1)
    assert "HELD" in out          # always included
    assert "A" in out and "B" in out  # top 2 bull
    assert "C" in out             # bottom 1 bear
    assert out[0] == "HELD"       # holdings first
    assert len(out) == len(set(out))  # de-duplicated


def test_build_indicators_empty_df():
    assert build_indicators(pd.DataFrame()) == {}


def test_build_indicators_keys_present():
    closes = [100 + i for i in range(220)]          # uptrend -> above_sma200
    df = pd.DataFrame({"Close": closes, "Volume": [1000] * 220})
    ind = build_indicators(df)
    assert set(ind) >= {"price", "rsi", "macd_hist", "above_sma200",
                        "golden_cross", "death_cross", "volume_spike"}
    assert ind["price"] == 319.0
    assert ind["above_sma200"] is True


# --- apply_sector_cap tests ---

def test_sector_cap_limits_non_held_per_sector():
    """At most max_per_sector non-held tickers from each sector are kept."""
    sectors = {"A": "Tech", "B": "Tech", "C": "Tech", "D": "Tech", "E": "Finance"}
    tickers = ["A", "B", "C", "D", "E"]
    result = apply_sector_cap(tickers, sectors.get, holdings=[], max_per_sector=2)
    tech_kept = [t for t in result if sectors.get(t) == "Tech"]
    assert len(tech_kept) <= 2
    assert "E" in result  # Finance ticker always passes


def test_sector_cap_always_keeps_holdings():
    """Holdings are never dropped even when their sector is over the cap."""
    sectors = {"A": "Tech", "B": "Tech", "C": "Tech", "HELD": "Tech"}
    tickers = ["HELD", "A", "B", "C"]
    result = apply_sector_cap(tickers, sectors.get, holdings=["HELD"], max_per_sector=1)
    assert "HELD" in result
    # Non-held Tech tickers should be capped at 1
    non_held_tech = [t for t in result if t != "HELD" and sectors.get(t) == "Tech"]
    assert len(non_held_tech) <= 1


def test_sector_cap_none_sector_never_dropped():
    """Tickers where sector_fn returns None are never dropped regardless of count."""
    def sector_fn(t):
        return None  # unknown sector for all

    tickers = ["A", "B", "C", "D", "E"]
    result = apply_sector_cap(tickers, sector_fn, holdings=[], max_per_sector=1)
    assert result == tickers  # all retained, order preserved
