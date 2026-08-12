"""Tests verifying per-agent log and report directory namespacing."""
import json

import config
from memory.report import write_run_report


def _minimal_report_kwargs():
    return dict(
        portfolio={"total_equity": 100000, "available_cash": 100000,
                   "trade_tokens": 20, "holdings": {}},
        scan_total=500,
        scored=500,
        candidates=[],
        macro={},
        transcript={"analyst": "", "bull": "", "bear": ""},
        actions=[],
        executed=[],
        mode="normal",
    )


def test_report_uses_agent_subdir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "AGENT_NAME", "momentum-mike")
    monkeypatch.setattr(config, "LOG_DIR", tmp_path)

    path = write_run_report("2026-07-05", **_minimal_report_kwargs())

    assert "momentum-mike" in str(path)
    assert path.exists()
    data = json.loads(path.read_text())
    assert data["run_date"] == "2026-07-05"


def test_default_agent_no_subdir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "AGENT_NAME", "default")
    monkeypatch.setattr(config, "LOG_DIR", tmp_path)

    path = write_run_report("2026-07-05", **_minimal_report_kwargs())

    assert "momentum-mike" not in str(path)
    assert path.parent == tmp_path
    assert path.exists()
