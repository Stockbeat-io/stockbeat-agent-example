from execution.stockbeat_client import StockbeatClient


class FakeResp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, get_payload=None, post_resp=None):
        self.get_payload = get_payload or {}
        self.post_resp = post_resp
        self.post_called = False
        self.last_post = None

    def get(self, url, headers=None, timeout=None):
        return FakeResp(self.get_payload)

    def post(self, url, headers=None, json=None, timeout=None):
        self.post_called = True
        self.last_post = json
        return self.post_resp


def test_get_universe_returns_set():
    sess = FakeSession(get_payload={"tickers": ["AAPL", "MSFT"]})
    client = StockbeatClient("k", "http://x", dry_run=True, session=sess)
    assert client.get_universe() == {"AAPL", "MSFT"}


def test_get_portfolio_parses_json():
    payload = {"available_cash": 50, "total_equity": 100, "holdings": {},
               "trade_tokens": 4, "locked_cash": 0}
    sess = FakeSession(get_payload=payload)
    client = StockbeatClient("k", "http://x", dry_run=True, session=sess)
    assert client.get_portfolio()["trade_tokens"] == 4


def test_submit_trade_dry_run_makes_no_post():
    sess = FakeSession()
    client = StockbeatClient("k", "http://x", dry_run=True, session=sess)
    out = client.submit_trade("BUY", "AAPL", usd_amount=5000, why="x" * 250,
                              target_price=210, target_horizon_days=30)
    assert sess.post_called is False
    assert out["status"] == "dry_run"
    assert out["ticker"] == "AAPL"


def test_submit_trade_live_posts_and_returns_json():
    sess = FakeSession(post_resp=FakeResp({"id": 201, "status": "EXECUTED"}, 201))
    client = StockbeatClient("k", "http://x", dry_run=False, session=sess)
    out = client.submit_trade("BUY", "AAPL", usd_amount=5000, why="x" * 250,
                              target_price=210, target_horizon_days=30)
    assert sess.post_called is True
    assert out["id"] == 201
    assert sess.last_post["why"] == "x" * 250


def test_submit_trade_live_error_response():
    sess = FakeSession(post_resp=FakeResp(
        {"status": "error", "error_code": "ERR_INSUFFICIENT_CASH"}, 400))
    client = StockbeatClient("k", "http://x", dry_run=False, session=sess)
    out = client.submit_trade("BUY", "AAPL", usd_amount=5000, why="x" * 250,
                              target_price=210, target_horizon_days=30)
    assert out["status"] == "error"
    assert out["error_code"] == "ERR_INSUFFICIENT_CASH"
