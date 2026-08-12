import data.news as news


def test_get_news_nested_format(monkeypatch):
    """yfinance now returns items with nested content.title."""
    class FakeTicker:
        def __init__(self, s):
            self.news = [
                {"id": "1", "content": {"title": "A"}},
                {"id": "2", "content": {"title": "B"}},
                {"id": "3", "content": {"title": "C"}},
            ]
    monkeypatch.setattr(news.yf, "Ticker", FakeTicker)
    assert news.get_news("AAPL", limit=2) == ["A", "B"]


def test_get_news_flat_format(monkeypatch):
    """Older yfinance versions return flat {title: ...} dicts."""
    class FakeTicker:
        def __init__(self, s):
            self.news = [{"title": "X"}, {"title": "Y"}]
    monkeypatch.setattr(news.yf, "Ticker", FakeTicker)
    assert news.get_news("AAPL") == ["X", "Y"]


def test_get_news_empty_on_error(monkeypatch):
    class Boom:
        def __init__(self, s):
            raise RuntimeError("x")
    monkeypatch.setattr(news.yf, "Ticker", Boom)
    assert news.get_news("AAPL") == []
