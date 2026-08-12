import json

import config
from memory.report import write_run_report


def test_write_run_report_creates_json(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "LOG_DIR", tmp_path)
    monkeypatch.setattr(config, "AGENT_NAME", "default")

    candidates = [{
        "ticker": "AAPL", "price": 200.0, "score": 5,
        "rsi": 45.0, "macd_hist": 1.0, "above_sma200": True,
        "_data_health": {
            "indicators": True, "fundamentals": True,
            "news": True, "stocktwits": False, "reddit": True,
        },
    }]
    executed = [{"action": "BUY", "ticker": "AAPL", "usd_amount": 5000}]
    transcript = {"analyst": "report text", "bull": "buy AAPL", "bear": "risky"}

    path = write_run_report(
        "2026-07-01", portfolio={"total_equity": 100000, "available_cash": 80000,
                                 "trade_tokens": 5, "holdings": {}},
        scan_total=500, scored=490, candidates=candidates,
        macro={"fed_funds": 5.0}, transcript=transcript,
        actions=[{"action": "BUY", "ticker": "AAPL"}],
        executed=executed, mode="normal")

    assert path.exists()
    data = json.loads(path.read_text())
    assert data["run_date"] == "2026-07-01"
    assert data["debate"]["analyst"] == "report text"
    assert data["data_health"]["all_sources_ok"] == 0  # stocktwits failed
    assert data["data_health"]["partial_data"] == 1
    assert data["data_health"]["macro_available"] is True
    assert data["execution"]["trades_executed"] == 1
    assert data["screening"]["shortlisted"] == 1


def test_write_run_report_all_sources_ok(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "LOG_DIR", tmp_path)
    monkeypatch.setattr(config, "AGENT_NAME", "default")

    candidates = [{
        "ticker": "MSFT", "price": 400.0, "score": 3,
        "_data_health": {
            "indicators": True, "fundamentals": True,
            "news": True, "stocktwits": True, "reddit": True,
        },
    }]

    path = write_run_report(
        "2026-07-01", portfolio={"total_equity": 100000, "available_cash": 80000,
                                 "trade_tokens": 5, "holdings": {}},
        scan_total=500, scored=490, candidates=candidates,
        macro={}, transcript={"analyst": "", "bull": "", "bear": ""},
        actions=[], executed=[], mode="initial")

    data = json.loads(path.read_text())
    assert data["data_health"]["all_sources_ok"] == 1
    assert data["data_health"]["macro_available"] is False
