import pandas as pd
import pytest

import data.macro as macro_mod


class FakeClient:
    """Injectable FRED client stub."""

    def __init__(self, series_map: dict):
        self._map = series_map

    def get_series(self, sid: str) -> pd.Series:
        return self._map[sid]


def test_get_macro_builds_snapshot():
    """Returns all four keys with correct values, including computed CPI YoY."""
    from config import FRED_SERIES

    # 14 monthly CPI values: index -13 = 300.0, index -1 = 315.0 → YoY = 5.0 %
    cpi_values = [300.0] * 13 + [315.0]

    fake = FakeClient(
        {
            FRED_SERIES["fed_funds"]: pd.Series([5.33]),
            FRED_SERIES["cpi"]: pd.Series(cpi_values),
            FRED_SERIES["ten_year"]: pd.Series([4.25]),
            FRED_SERIES["gdp_growth"]: pd.Series([2.1]),
        }
    )

    result = macro_mod.get_macro(client=fake)

    assert set(result.keys()) == {"fed_funds", "cpi_yoy", "ten_year", "gdp_growth"}
    assert result["fed_funds"] == pytest.approx(5.33)
    assert result["cpi_yoy"] == pytest.approx(5.0)  # (315/300 - 1) * 100
    assert result["ten_year"] == pytest.approx(4.25)
    assert result["gdp_growth"] == pytest.approx(2.1)


def test_get_macro_empty_without_key(monkeypatch):
    """Returns {} when no FRED API key is available and no client injected."""
    monkeypatch.setattr("config.FRED_API_KEY", "")
    result = macro_mod.get_macro(api_key="")
    assert result == {}


def test_get_macro_empty_on_error():
    """Returns {} when the client raises any exception."""

    class BoomClient:
        def get_series(self, sid: str):
            raise RuntimeError("network down")

    result = macro_mod.get_macro(client=BoomClient())
    assert result == {}
