from unittest.mock import patch

from execution.mcp_client import McpStockbeatClient


def _make_client():
    return McpStockbeatClient("sk_live_test", "http://localhost:3000", dry_run=True)


# --- Read tools ---

def test_get_portfolio_returns_dict():
    client = _make_client()
    portfolio = {"available_cash": 50000, "total_equity": 100000,
                 "holdings": {}, "trade_tokens": 5}
    with patch.object(client, "_call_tool", return_value=portfolio):
        assert client.get_portfolio()["trade_tokens"] == 5


def test_get_universe_returns_set():
    client = _make_client()
    with patch.object(client, "_call_tool", return_value={"tickers": ["AAPL", "MSFT"]}):
        result = client.get_universe()
        assert result == {"AAPL", "MSFT"}


def test_get_pending_orders_returns_list():
    client = _make_client()
    orders = [{"id": "1", "action": "STOP_LOSS", "ticker": "AAPL"}]
    with patch.object(client, "_call_tool", return_value={"orders": orders}):
        result = client.get_pending_orders()
        assert len(result) == 1
        assert result[0]["ticker"] == "AAPL"


def test_get_quota_returns_dict():
    client = _make_client()
    quota = {"remaining": 15, "capacity": 20}
    with patch.object(client, "_call_tool", return_value=quota):
        assert client.get_quota()["remaining"] == 15


def test_get_trade_history_passes_page():
    client = _make_client()
    history = {"trades": [], "page": 2}
    with patch.object(client, "_call_tool", return_value=history) as mock:
        client.get_trade_history(page=2)
        mock.assert_called_once_with("list_trades", {"page": 2})


def test_list_comments_inbox_mode():
    client = _make_client()
    comments = {"comments": [{"id": "c1", "body": "nice trade"}]}
    with patch.object(client, "_call_tool", return_value=comments) as mock:
        result = client.list_comments()
        mock.assert_called_once_with("list_comments", {"limit": 20})
        assert result["comments"][0]["id"] == "c1"


def test_list_comments_with_trade_id():
    client = _make_client()
    with patch.object(client, "_call_tool", return_value={"comments": []}) as mock:
        client.list_comments(trade_id="t1", limit=10)
        mock.assert_called_once_with("list_comments", {"trade_id": "t1", "limit": 10})


# --- Write tools ---

def test_submit_trade_dry_run_skips_call():
    client = _make_client()
    with patch.object(client, "_call_tool") as mock:
        result = client.submit_trade("BUY", "AAPL", usd_amount=5000,
                                     why="x" * 250, target_price=210,
                                     target_horizon_days=30)
        mock.assert_not_called()
    assert result["status"] == "dry_run"
    assert result["ticker"] == "AAPL"


def test_submit_trade_live_calls_buy_tool():
    client = McpStockbeatClient("sk_live_test", "http://localhost:3000",
                                 dry_run=False)
    expected = {"id": "t1", "status": "PENDING", "action": "BUY"}
    with patch.object(client, "_call_tool", return_value=expected) as mock:
        result = client.submit_trade("BUY", "AAPL", usd_amount=5000,
                                     why="x" * 250, target_price=210,
                                     target_horizon_days=30)
        mock.assert_called_once_with("buy", {
            "ticker": "AAPL", "usd_amount": 5000, "why": "x" * 250,
            "target_price": 210, "target_horizon_days": 30,
        })
    assert result["status"] == "PENDING"


def test_submit_trade_maps_stop_loss():
    client = McpStockbeatClient("k", "http://x", dry_run=False)
    with patch.object(client, "_call_tool", return_value={"status": "ok"}) as mock:
        client.submit_trade("STOP_LOSS", "AAPL", limit_price=180.0,
                            why="x" * 250)
        mock.assert_called_once_with("set_stop_loss", {
            "ticker": "AAPL", "limit_price": 180.0, "why": "x" * 250,
        })


def test_submit_trade_maps_close_stock():
    client = McpStockbeatClient("k", "http://x", dry_run=False)
    with patch.object(client, "_call_tool", return_value={"status": "ok"}) as mock:
        client.submit_trade("CLOSE_STOCK", "AAPL", why="x" * 250)
        mock.assert_called_once_with("close_position", {
            "ticker": "AAPL", "why": "x" * 250,
        })


def test_submit_trade_unknown_action():
    client = _make_client()
    result = client.submit_trade("YOLO", "AAPL")
    assert result["status"] == "error"
    assert result["error_code"] == "ERR_UNKNOWN_ACTION"


def test_submit_trade_filters_none_args():
    client = McpStockbeatClient("k", "http://x", dry_run=False)
    with patch.object(client, "_call_tool", return_value={"status": "ok"}) as mock:
        client.submit_trade("SELL", "AAPL", usd_amount=3000, why="x" * 250,
                            target_price=None)
        args = mock.call_args[0][1]
        assert "target_price" not in args


def test_reply_to_comment_dry_run():
    client = _make_client()
    with patch.object(client, "_call_tool") as mock:
        result = client.reply_to_comment("c1", "thanks for the feedback")
        mock.assert_not_called()
    assert result["status"] == "dry_run"


def test_reply_to_comment_live():
    client = McpStockbeatClient("k", "http://x", dry_run=False)
    with patch.object(client, "_call_tool", return_value={"status": "ok"}) as mock:
        client.reply_to_comment("c1", "good point on the PE ratio")
        mock.assert_called_once_with("reply_to_comment", {
            "comment_id": "c1", "body": "good point on the PE ratio",
        })
