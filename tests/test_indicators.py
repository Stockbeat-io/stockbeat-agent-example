import math

import pandas as pd

from data.indicators import macd, rsi, sma


def test_sma_last_n_mean():
    s = pd.Series([1, 2, 3, 4, 5, 6, 7, 8, 9, 10])
    assert sma(s, 5) == 8.0  # mean(6,7,8,9,10)


def test_sma_too_short_is_nan():
    assert math.isnan(sma(pd.Series([1, 2]), 5))


def test_rsi_all_gains_is_100():
    s = pd.Series(list(range(1, 30)))  # strictly increasing
    assert rsi(s, 14) == 100.0


def test_rsi_too_short_is_nan():
    assert math.isnan(rsi(pd.Series([1, 2, 3]), 14))


def test_macd_keys_and_constant_series_near_zero():
    s = pd.Series([100.0] * 60)
    out = macd(s)
    assert set(out) == {"macd", "signal", "hist"}
    assert abs(out["macd"]) < 1e-9
    assert abs(out["hist"]) < 1e-9


def test_macd_too_short_is_nan():
    out = macd(pd.Series([1, 2, 3]))
    assert math.isnan(out["macd"])
