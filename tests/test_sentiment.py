import data.sentiment as sentiment


class FakeResp:
    def __init__(self, payload=None, text="", status=200):
        self._p = payload
        self.text = text
        self.status_code = status

    def json(self):
        return self._p


class FakeSession:
    def __init__(self, resp=None, boom=False):
        self.resp = resp
        self.boom = boom

    def get(self, url, timeout=None, headers=None):
        if self.boom:
            raise RuntimeError("timeout")
        return self.resp


def test_stocktwits_counts_sentiment():
    payload = {"messages": [
        {"entities": {"sentiment": {"basic": "Bullish"}}},
        {"entities": {"sentiment": {"basic": "Bearish"}}},
        {"entities": {"sentiment": {"basic": "Bullish"}}},
        {"entities": {"sentiment": None}},
    ]}
    out = sentiment.get_stocktwits("AAPL", session=FakeSession(FakeResp(payload)))
    assert out == {"bullish": 2, "bearish": 1, "messages": 4}


def test_stocktwits_empty_on_error():
    assert sentiment.get_stocktwits("AAPL", session=FakeSession(boom=True)) == {}


def test_stocktwits_empty_on_non_200():
    out = sentiment.get_stocktwits("AAPL", session=FakeSession(FakeResp({}, status=429)))
    assert out == {}


_RSS_BODY = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry><title>AAPL to moon</title></entry>
  <entry><title>buying more AAPL</title></entry>
</feed>"""


def test_reddit_counts_mentions():
    out = sentiment.get_reddit("AAPL",
                               session=FakeSession(FakeResp(text=_RSS_BODY)))
    assert out == {"mentions": 2}


def test_reddit_empty_on_error():
    assert sentiment.get_reddit("AAPL", session=FakeSession(boom=True)) == {}


def test_reddit_empty_on_non_200():
    out = sentiment.get_reddit("AAPL",
                               session=FakeSession(FakeResp(text="", status=403)))
    assert out == {}
