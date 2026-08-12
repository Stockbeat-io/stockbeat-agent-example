from datetime import datetime
from zoneinfo import ZoneInfo

from clock import is_market_open

ET = ZoneInfo("America/New_York")


def test_open_midday_weekday():
    assert is_market_open(datetime(2026, 7, 1, 12, 0, tzinfo=ET)) is True


def test_closed_before_open():
    assert is_market_open(datetime(2026, 7, 1, 9, 0, tzinfo=ET)) is False


def test_open_at_930():
    assert is_market_open(datetime(2026, 7, 1, 9, 30, tzinfo=ET)) is True


def test_closed_at_close():
    assert is_market_open(datetime(2026, 7, 1, 16, 0, tzinfo=ET)) is False


def test_closed_weekend():
    # 2026-07-04 is a Saturday
    assert is_market_open(datetime(2026, 7, 4, 12, 0, tzinfo=ET)) is False
