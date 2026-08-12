from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pandas as pd

import config
import main

ET = ZoneInfo("America/New_York")
OPEN = datetime(2026, 7, 1, 12, 0, tzinfo=ET)


class FakeClient:
    def __init__(self, portfolio, universe):
        self._portfolio = portfolio
        self._universe = universe
        self.submitted = []
        self.dry_run = True

    def get_portfolio(self):
        return self._portfolio

    def get_universe(self):
        return self._universe

    def get_pending_orders(self):
        return []

    def submit_trade(self, action, ticker, **kw):
        self.submitted.append((action, ticker))
        return {"status": "dry_run", "ticker": ticker}

    def list_comments(self, **kw):
        return {"comments": []}

    def reply_to_comment(self, comment_id, body):
        return {"status": "dry_run"}


class FakeLLM:
    def __init__(self, response):
        self.response = response

    def generate(self, prompt, system=None):
        return self.response


def _patch_data(monkeypatch):
    closes = [100 + i for i in range(220)]
    df = pd.DataFrame({"Close": closes, "Volume": [1000] * 220})
    monkeypatch.setattr(main, "batch_download",
                        lambda tickers, **k: {t: df for t in tickers})
    sectors = {"AAPL": "Tech", "MSFT": "Tech", "NVDA": "Tech",
                "JPM": "Finance", "JNJ": "Health", "XOM": "Energy",
                "PG": "Consumer", "WMT": "Retail"}
    monkeypatch.setattr(main, "get_sector", lambda t: sectors.get(t, "Other"))
    monkeypatch.setattr(main, "get_macro", lambda: {"fed_funds": 5.0})
    monkeypatch.setattr(main, "enrich_candidates",
                        lambda tickers, ind, scores: [
                            {"ticker": t, "price": 319.0, "score": scores.get(t, 0),
                             "rsi": 100.0, "macd_hist": 1.0, "above_sma200": True,
                             "pe": 25.0, "revenue_growth": 0.1, "news": [],
                             "stocktwits": {}, "reddit": {}} for t in tickers])
    monkeypatch.setattr(main, "_current_price", lambda t: 319.0)
    monkeypatch.setattr(main, "get_history", lambda t, **k: df)
    monkeypatch.setattr(main, "recent_lessons", lambda path, ticker: "")
    monkeypatch.setattr(main, "grade_open_decisions",
                        lambda *a, **k: {"graded": 0, "checkpoints": 0})
    monkeypatch.setattr(main, "high_water_since", lambda t, since: None)


def test_run_exits_when_no_tokens(monkeypatch):
    client = FakeClient(
        {"total_equity": 100_000, "available_cash": 100_000, "holdings": {},
         "trade_tokens": 0}, {"AAPL"})
    out = main.run(now_et=OPEN, client=client, llm=FakeLLM("[]"))
    assert out["status"] == "no_tokens"
    assert client.submitted == []



def test_stops_are_managed_even_with_no_trade_tokens(monkeypatch, tmp_path):
    """Stop updates cost no tokens, so an exhausted agent still gets protected."""
    _patch_data(monkeypatch)
    monkeypatch.setattr(main, "_memory_path", lambda: tmp_path / "d.jsonl")
    client = FakeClient(
        {"total_equity": 100_000, "available_cash": 0,
         "holdings": {"AAPL": {"avg_price": 200.0}},
         "trade_tokens": 0}, {"AAPL"})
    out = main.run(now_et=OPEN, client=client, llm=FakeLLM("[]"))

    assert out["status"] == "no_tokens"
    # price 319 vs entry 200 is deep in the trail band -> a stop must be placed
    assert [s[0] for s in client.submitted] == ["STOP_LOSS"]
    assert out["stops"]["placed"] == 1


def test_no_tokens_still_skips_trading(monkeypatch, tmp_path):
    _patch_data(monkeypatch)
    monkeypatch.setattr(main, "_memory_path", lambda: tmp_path / "d.jsonl")
    client = FakeClient(
        {"total_equity": 100_000, "available_cash": 100_000, "holdings": {},
         "trade_tokens": 0}, {"AAPL"})
    out = main.run(now_et=OPEN, client=client, llm=FakeLLM("[]"))
    assert out["trades"] == 0
    assert client.submitted == []


def test_run_executes_validated_buy(monkeypatch, tmp_path):
    _patch_data(monkeypatch)
    monkeypatch.setattr(main, "_memory_path", lambda: tmp_path / "decisions.jsonl")
    universe = set(main.config.SKELETON_TICKERS)
    client = FakeClient(
        {"total_equity": 100_000, "available_cash": 100_000, "holdings": {},
         "trade_tokens": 5}, universe)
    llm = FakeLLM(
        '[{"action": "BUY", "ticker": "AAPL", "usd_amount": 5000, '
        '"target_price": 400.0, "target_horizon_days": 30, "why": "short"}]')
    out = main.run(now_et=OPEN, client=client, llm=llm)
    assert out["status"] == "ok"
    assert out["trades"] == 1
    assert client.submitted[0] == ("BUY", "AAPL")
    assert (tmp_path / "decisions.jsonl").exists()


def test_run_caps_to_max_actions(monkeypatch, tmp_path):
    _patch_data(monkeypatch)
    monkeypatch.setattr(main, "_memory_path", lambda: tmp_path / "decisions.jsonl")
    universe = set(main.config.SKELETON_TICKERS)
    client = FakeClient(
        {"total_equity": 1_000_000, "available_cash": 1_000_000, "holdings": {},
         "trade_tokens": 5}, universe)
    actions = ", ".join(
        '{"action": "BUY", "ticker": "%s", "usd_amount": 5000, '
        '"target_price": 400.0, "target_horizon_days": 30, "why": "ok"}' % t
        for t in ["AAPL", "MSFT", "NVDA", "JPM", "XOM"])
    out = main.run(now_et=OPEN, client=client, llm=FakeLLM("[" + actions + "]"))
    assert out["trades"] == main.config.MAX_TRADES_NORMAL  # 3


def test_deferred_stop_loss_placed_on_next_run(monkeypatch, tmp_path):
    """Stop-losses are placed at the start of the NEXT run for held positions."""
    _patch_data(monkeypatch)
    mem = tmp_path / "d.jsonl"
    monkeypatch.setattr(main, "_memory_path", lambda: mem)
    universe = set(main.config.SKELETON_TICKERS)
    client = FakeClient(
        {"total_equity": 100_000, "available_cash": 90_000,
         "holdings": {"AAPL": {"shares": 10}},
         "trade_tokens": 5}, universe)
    import json
    mem.write_text(json.dumps({"ticker": "AAPL", "action": "BUY",
                               "stop_loss_price": 300.0, "date": "2026-07-01",
                               "status": "pending"}) + "\n")
    llm = FakeLLM('{"risk_assessment": {}, "actions": []}')
    main.run(now_et=OPEN, client=client, llm=llm)
    kinds = [s[0] for s in client.submitted]
    assert "STOP_LOSS" in kinds


def test_initial_mode_allows_more_actions(monkeypatch, tmp_path):
    _patch_data(monkeypatch)
    monkeypatch.setattr(main, "_memory_path", lambda: tmp_path / "d.jsonl")
    universe = set(main.config.SKELETON_TICKERS)
    client = FakeClient(
        {"total_equity": 1_000_000, "available_cash": 1_000_000, "holdings": {},
         "trade_tokens": 20}, universe)  # 100% cash + tokens>=15 -> initial
    actions = ", ".join(
        '{"action": "BUY", "ticker": "%s", "usd_amount": 5000, '
        '"target_price": 400.0, "target_horizon_days": 30, "why": "ok"}' % t
        for t in main.config.SKELETON_TICKERS)
    out = main.run(now_et=OPEN, client=client, llm=FakeLLM("[" + actions + "]"))
    assert out["trades"] > main.config.MAX_TRADES_NORMAL  # more than 3 in initial build


def test_stop_loss_price_recorded_in_decision_log(monkeypatch, tmp_path):
    """Stop-loss price is saved in the decision log for deferred placement."""
    _patch_data(monkeypatch)
    mem = tmp_path / "d.jsonl"
    monkeypatch.setattr(main, "_memory_path", lambda: mem)
    universe = set(main.config.SKELETON_TICKERS)
    client = FakeClient(
        {"total_equity": 100_000, "available_cash": 100_000, "holdings": {},
         "trade_tokens": 5}, universe)
    llm = FakeLLM(
        '[{"action": "BUY", "ticker": "AAPL", "usd_amount": 5000, '
        '"target_price": 400.0, "target_horizon_days": 30, "why": "ok"}]')
    main.run(now_et=OPEN, client=client, llm=llm)
    import json
    records = [json.loads(l) for l in mem.read_text().splitlines() if l.strip()]
    buy_rec = [r for r in records if r.get("action") == "BUY"]
    assert buy_rec
    assert buy_rec[0].get("stop_loss_price") is not None


def test_run_uses_risk_assessment_from_judge(monkeypatch, tmp_path):
    _patch_data(monkeypatch)
    monkeypatch.setattr(main, "_memory_path", lambda: tmp_path / "d.jsonl")
    universe = set(main.config.SKELETON_TICKERS)
    client = FakeClient(
        {"total_equity": 100_000, "available_cash": 100_000, "holdings": {},
         "trade_tokens": 5}, universe)
    llm = FakeLLM(
        '{"risk_assessment": {"stance": "aggressive", "cash_target_pct": 12, '
        '"default_position_pct": 18, "reasoning": "bull run"}, '
        '"actions": [{"action": "BUY", "ticker": "AAPL", "usd_amount": 20000, '
        '"target_price": 400.0, "target_horizon_days": 30, "why": "ok", '
        '"stop_loss_price": 300.0}]}')
    out = main.run(now_et=OPEN, client=client, llm=llm)
    assert out["status"] == "ok"
    assert out["trades"] == 1
    assert client.submitted[0] == ("BUY", "AAPL")


def test_stop_loss_clamps_invalid_price_in_log(monkeypatch, tmp_path):
    """Invalid stop_loss_price from LLM gets clamped to default in decision log."""
    _patch_data(monkeypatch)
    mem = tmp_path / "d.jsonl"
    monkeypatch.setattr(main, "_memory_path", lambda: mem)
    universe = set(main.config.SKELETON_TICKERS)
    client = FakeClient(
        {"total_equity": 100_000, "available_cash": 100_000, "holdings": {},
         "trade_tokens": 5}, universe)
    llm = FakeLLM(
        '[{"action": "BUY", "ticker": "AAPL", "usd_amount": 5000, '
        '"target_price": 400.0, "target_horizon_days": 30, "why": "ok", '
        '"stop_loss_price": 999.0}]')
    main.run(now_et=OPEN, client=client, llm=llm)
    import json
    records = [json.loads(l) for l in mem.read_text().splitlines() if l.strip()]
    buy_rec = [r for r in records if r.get("action") == "BUY"][0]
    # 999 is way above entry price, so it should clamp to default (6% below 319)
    expected_default = round(319.0 * (1 - main.config.STOP_LOSS_DEFAULT_PCT), 2)
    assert buy_rec["stop_loss_price"] == expected_default


def test_run_with_agent_sets_config(monkeypatch):
    """Verify --agent loads the profile and overrides config."""
    # Register current values with monkeypatch so they are restored after the test,
    # preventing config mutation from leaking into subsequent tests.
    monkeypatch.setattr(config, "AGENT_NAME", config.AGENT_NAME)
    monkeypatch.setattr(config, "AGENT_PERSONA", config.AGENT_PERSONA)
    monkeypatch.setattr(config, "STOCKBEAT_API_KEY", config.STOCKBEAT_API_KEY)
    monkeypatch.setattr(config, "CASH_TARGET_DEFAULT", config.CASH_TARGET_DEFAULT)
    monkeypatch.setattr(config, "POSITION_PCT_DEFAULT", config.POSITION_PCT_DEFAULT)
    monkeypatch.setattr(config, "STOP_LOSS_DEFAULT_PCT", config.STOP_LOSS_DEFAULT_PCT)

    fake_profile = {
        "name": "momentum-mike",
        "display_name": "Momentum Mike",
        "api_key": "sk_test_123",
        "persona": "You are Momentum Mike — an aggressive momentum trader." + " " * 50,
        "schedule_hour": 17,
        "schedule_minute": 0,
        "risk_overrides": {
            "cash_target_default": 15,
            "position_pct_default": 15,
            "stop_loss_default_pct": 0.04,
        },
    }
    # main.py uses `from profiles import load_profile`, so the correct patch target
    # is main.load_profile (not profiles.load_profile).
    with patch("main.load_profile", return_value=fake_profile):
        main._apply_agent_profile("momentum-mike")
        assert config.AGENT_NAME == "momentum-mike"
        assert config.AGENT_PERSONA == fake_profile["persona"]
        assert config.STOCKBEAT_API_KEY == "sk_test_123"
        assert config.CASH_TARGET_DEFAULT == 15
        assert config.POSITION_PCT_DEFAULT == 15
        assert config.STOP_LOSS_DEFAULT_PCT == 0.04


def test_handle_comment_replies_filters_old_and_replied(monkeypatch, tmp_path):
    from datetime import timedelta
    now_et = datetime(2026, 7, 15, 12, 0, tzinfo=ET)
    fresh = (now_et - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    stale = (now_et - timedelta(hours=25)).strftime("%Y-%m-%dT%H:%M:%SZ")

    comments_data = {"comments": [
        {"id": "c1", "body": "Great trade!", "replied": False,
         "created_at": fresh, "comments_count": 5,
         "trade_id": "t1", "trade_ticker": "AAPL",
         "trade_action": "BUY", "trade_why": "Strong signals."},
        {"id": "c2", "body": "Old one", "replied": False,
         "created_at": stale, "comments_count": 1,
         "trade_id": "t2", "trade_ticker": "MSFT",
         "trade_action": "SELL", "trade_why": "Weak."},
        {"id": "c3", "body": "Already answered", "replied": True,
         "created_at": fresh, "comments_count": 3,
         "trade_id": "t3", "trade_ticker": "NVDA",
         "trade_action": "BUY", "trade_why": "AI play."},
    ]}

    class MockClient:
        dry_run = True
        def list_comments(self, **kw): return comments_data
        def reply_to_comment(self, cid, body): return {"status": "dry_run"}

    llm = FakeLLM('[{"comment_id": "c1", "reply": "Thanks for the feedback!"}]')
    replies = main._handle_comment_replies(MockClient(), llm, now_et)
    assert len(replies) == 1
    assert replies[0]["comment_id"] == "c1"


def test_handle_comment_replies_skips_when_no_eligible(monkeypatch, tmp_path):
    now_et = datetime(2026, 7, 15, 12, 0, tzinfo=ET)

    class MockClient:
        dry_run = True
        def list_comments(self, **kw): return {"comments": []}

    llm = FakeLLM("should not be called")
    replies = main._handle_comment_replies(MockClient(), llm, now_et)
    assert replies == []


def test_full_run_with_agent_profile(monkeypatch):
    """End-to-end: agent profile loads, persona injects, state is namespaced."""
    # Save current config values to restore after the test
    monkeypatch.setattr(config, "AGENT_NAME", config.AGENT_NAME)
    monkeypatch.setattr(config, "AGENT_PERSONA", config.AGENT_PERSONA)
    monkeypatch.setattr(config, "STOCKBEAT_API_KEY", config.STOCKBEAT_API_KEY)
    monkeypatch.setattr(config, "CASH_TARGET_DEFAULT", config.CASH_TARGET_DEFAULT)
    monkeypatch.setattr(config, "POSITION_PCT_DEFAULT", config.POSITION_PCT_DEFAULT)
    monkeypatch.setattr(config, "STOP_LOSS_DEFAULT_PCT", config.STOP_LOSS_DEFAULT_PCT)

    main._apply_agent_profile("steady-eddie")

    assert config.AGENT_NAME == "steady-eddie"
    assert "Steady Eddie" in config.AGENT_PERSONA
    assert config.CASH_TARGET_DEFAULT == 35
    assert config.POSITION_PCT_DEFAULT == 8

    mem_dir = config.agent_memory_dir()
    assert "steady-eddie" in str(mem_dir)

    log_dir = config.agent_log_dir()
    assert "steady-eddie" in str(log_dir)
