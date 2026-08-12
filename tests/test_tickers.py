from tickers import normalize_ticker


def test_dot_to_dash():
    assert normalize_ticker("BRK.B") == "BRK-B"


def test_slash_to_dash():
    assert normalize_ticker("bf/b") == "BF-B"


def test_plain_uppercased_and_trimmed():
    assert normalize_ticker("  aapl ") == "AAPL"


def test_already_canonical():
    assert normalize_ticker("BRK-B") == "BRK-B"
