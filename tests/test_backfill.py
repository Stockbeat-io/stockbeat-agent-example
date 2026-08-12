import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import backfill  # noqa: E402


def _agent_log(root, agent, records):
    path = root / agent / "memory" / "decisions.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")
    return path


def _stub_market(monkeypatch, ret=10.0, spy=4.0, complete=(5,)):
    def window_return(ticker, date, days):
        if days not in complete:
            return None
        return spy if ticker == "SPY" else ret

    monkeypatch.setattr(backfill, "window_return", window_return)
    monkeypatch.setattr(backfill, "excursion_since", lambda t, d, e: (6.0, -1.0))


def test_backfill_grades_every_agent(tmp_path, monkeypatch):
    _stub_market(monkeypatch)
    _agent_log(tmp_path, "a", [{"ticker": "AAPL", "action": "BUY",
                                "date": "2026-07-01", "status": "pending"}])
    _agent_log(tmp_path, "b", [{"ticker": "MSFT", "action": "BUY",
                                "date": "2026-07-01", "status": "pending"}])
    results = backfill.backfill(tmp_path, "2026-08-01")
    assert results["a"]["checkpoints"] == 1
    assert results["b"]["checkpoints"] == 1


def test_backfill_writes_alpha_into_the_log(tmp_path, monkeypatch):
    _stub_market(monkeypatch)
    path = _agent_log(tmp_path, "a", [{"ticker": "AAPL", "action": "BUY",
                                       "date": "2026-07-01", "status": "pending"}])
    backfill.backfill(tmp_path, "2026-08-01")
    record = json.loads(path.read_text().strip())
    assert record["checkpoints"]["5d"]["alpha_pct"] == 6.0


def test_backfill_is_idempotent(tmp_path, monkeypatch):
    _stub_market(monkeypatch)
    _agent_log(tmp_path, "a", [{"ticker": "AAPL", "action": "BUY",
                                "date": "2026-07-01", "status": "pending"}])
    backfill.backfill(tmp_path, "2026-08-01")
    second = backfill.backfill(tmp_path, "2026-08-01")
    assert second["a"]["checkpoints"] == 0


def test_backfill_does_not_overwrite_existing_checkpoints(tmp_path, monkeypatch):
    _stub_market(monkeypatch)
    path = _agent_log(tmp_path, "a", [{"ticker": "AAPL", "action": "BUY",
                                       "date": "2026-07-01", "status": "open"}])
    backfill.backfill(tmp_path, "2026-08-01")
    _stub_market(monkeypatch, ret=99.0)
    backfill.backfill(tmp_path, "2026-08-01")
    record = json.loads(path.read_text().strip())
    assert record["checkpoints"]["5d"]["return_pct"] == 10.0


def test_backfill_leaves_features_null(tmp_path, monkeypatch):
    # historical evidence was never recorded and must not be invented
    _stub_market(monkeypatch)
    path = _agent_log(tmp_path, "a", [{"ticker": "AAPL", "action": "BUY",
                                       "date": "2026-07-01", "status": "pending"}])
    backfill.backfill(tmp_path, "2026-08-01")
    assert json.loads(path.read_text().strip()).get("features") is None


def test_dry_run_writes_nothing(tmp_path, monkeypatch):
    _stub_market(monkeypatch)
    path = _agent_log(tmp_path, "a", [{"ticker": "AAPL", "action": "BUY",
                                       "date": "2026-07-01", "status": "pending"}])
    before = path.read_text()
    results = backfill.backfill(tmp_path, "2026-08-01", dry_run=True)
    assert results["a"] == {"would_read": 1, "would_migrate": 0}
    assert path.read_text() == before


# --- legacy migration ---

def _legacy(ticker="AAPL"):
    return {"ticker": ticker, "action": "BUY", "date": "2026-07-01",
            "status": "resolved", "entry_price": 100.0,
            "outcome": {"return_pct": 0.2, "spy_return_pct": None,
                        "alpha_pct": None}}


def test_legacy_resolved_records_are_reopened_and_graded(tmp_path, monkeypatch):
    _stub_market(monkeypatch)
    path = _agent_log(tmp_path, "a", [_legacy()])
    results = backfill.backfill(tmp_path, "2026-08-01")
    assert results["a"]["migrated"] == 1
    assert results["a"]["checkpoints"] == 1
    assert json.loads(path.read_text().strip())["checkpoints"]["5d"]["alpha_pct"] == 6.0


def test_one_day_outcome_is_preserved_but_not_treated_as_a_grade(tmp_path, monkeypatch):
    _stub_market(monkeypatch)
    path = _agent_log(tmp_path, "a", [_legacy()])
    backfill.backfill(tmp_path, "2026-08-01")
    record = json.loads(path.read_text().strip())
    assert record["legacy_outcome"]["return_pct"] == 0.2
    assert "outcome" not in record


def test_migration_is_idempotent(tmp_path, monkeypatch):
    _stub_market(monkeypatch)
    _agent_log(tmp_path, "a", [_legacy()])
    backfill.backfill(tmp_path, "2026-08-01")
    assert backfill.backfill(tmp_path, "2026-08-01")["a"]["migrated"] == 0


def test_dry_run_counts_legacy_records(tmp_path, monkeypatch):
    _stub_market(monkeypatch)
    _agent_log(tmp_path, "a", [_legacy("AAPL"), _legacy("MSFT")])
    assert backfill.backfill(tmp_path, "2026-08-01",
                             dry_run=True)["a"]["would_migrate"] == 2


def test_main_returns_error_without_logs(tmp_path, capsys):
    assert backfill.main(["--root", str(tmp_path / "none")]) == 1
    assert "No decision logs" in capsys.readouterr().out


def test_main_reports_totals(tmp_path, monkeypatch, capsys):
    _stub_market(monkeypatch)
    _agent_log(tmp_path, "a", [{"ticker": "AAPL", "action": "BUY",
                                "date": "2026-07-01", "status": "pending"}])
    assert backfill.main(["--root", str(tmp_path)]) == 0
    assert "checkpoints written" in capsys.readouterr().out
