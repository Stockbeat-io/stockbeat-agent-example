import json

from memory.memory import (best_checkpoint, extract_features,
                           grade_open_decisions, log_decisions, recent_lessons)


def _records(path):
    return [json.loads(line) for line in path.read_text().strip().splitlines()]


def _grade(path, run_date, returns=None, excursion=(5.0, -2.0), complete=None):
    """Grade with a stub price feed.

    `returns` maps (ticker, trading_days) -> pct. `complete` limits which
    windows have elapsed; anything else returns None, as an incomplete window
    does in production.
    """
    returns = returns or {}

    def window_fn(ticker, date, days):
        if complete is not None and days not in complete:
            return None
        if (ticker, days) in returns:
            return returns[(ticker, days)]
        return 4.0 if ticker == "SPY" else 10.0

    return grade_open_decisions(path, window_fn=window_fn,
                                excursion_fn=lambda t, d, e: excursion,
                                run_date=run_date)


# --- log_decisions ---

def test_log_decisions_appends_jsonl(tmp_path):
    path = tmp_path / "sub" / "decisions.jsonl"
    decisions = [
        {"ticker": "AAPL", "action": "BUY", "usd_amount": 5000},
        {"ticker": "MSFT", "action": "SELL", "usd_amount": 3000},
    ]
    assert log_decisions(decisions, path=path, run_date="2026-07-01") == 2

    records = _records(path)
    assert len(records) == 2
    assert records[0]["ticker"] == "AAPL"
    assert records[0]["date"] == "2026-07-01"
    assert records[0]["status"] == "open"


def test_log_decisions_appends_not_overwrites(tmp_path):
    path = tmp_path / "decisions.jsonl"
    log_decisions([{"ticker": "A", "action": "BUY"}], path=path, run_date="2026-07-01")
    log_decisions([{"ticker": "B", "action": "BUY"}], path=path, run_date="2026-07-02")
    assert len(_records(path)) == 2


def test_log_decisions_does_not_mutate_input(tmp_path):
    decision = {"ticker": "A", "action": "BUY"}
    log_decisions([decision], path=tmp_path / "d.jsonl", run_date="2026-07-01")
    assert "status" not in decision and "date" not in decision


def test_log_decisions_stores_features(tmp_path):
    path = tmp_path / "d.jsonl"
    log_decisions([{"ticker": "AAPL", "action": "BUY"}], path=path,
                  run_date="2026-07-01",
                  features_by_ticker={"AAPL": {"rsi": 55.0, "sector": "Tech"}})
    assert _records(path)[0]["features"] == {"rsi": 55.0, "sector": "Tech"}


def test_log_decisions_features_default_to_null(tmp_path):
    path = tmp_path / "d.jsonl"
    log_decisions([{"ticker": "AAPL", "action": "BUY"}], path=path, run_date="2026-07-01")
    assert _records(path)[0]["features"] is None


# --- extract_features ---

def test_extract_features_pulls_evidence_off_candidate():
    candidate = {"ticker": "AAPL", "rsi": 61.2, "score": 8, "sector": "Tech",
                 "stocktwits": {"sentiment": 0.4}, "reddit": ["a", "b"],
                 "news": ["h1", "h2", "h3"], "price": 100.0}
    features = extract_features(candidate, extra={"mode": "normal"})
    assert features["rsi"] == 61.2
    assert features["stocktwits_sentiment"] == 0.4
    assert features["reddit_mentions"] == 2
    assert features["news_count"] == 3
    assert features["mode"] == "normal"
    assert "price" not in features  # price is on the decision, not evidence


def test_extract_features_omits_missing_values():
    assert "rsi" not in extract_features({"ticker": "AAPL", "rsi": None})


def test_extract_features_handles_no_candidate():
    assert extract_features(None, extra={"mode": "initial"}) == {"mode": "initial"}


# --- grading ---

def test_checkpoint_records_alpha_against_spy(tmp_path):
    path = tmp_path / "d.jsonl"
    log_decisions([{"ticker": "AAPL", "action": "BUY", "entry_price": 100.0}],
                  path=path, run_date="2026-06-01")
    result = _grade(path, "2026-07-01", complete={5})
    assert result["checkpoints"] == 1

    checkpoints = _records(path)[0]["checkpoints"]
    assert checkpoints["5d"] == {"return_pct": 10.0, "spy_return_pct": 4.0,
                                 "alpha_pct": 6.0}
    assert "20d" not in checkpoints  # window has not elapsed


def test_sell_alpha_is_sign_flipped(tmp_path):
    # selling something that then underperformed SPY is a good call
    path = tmp_path / "d.jsonl"
    log_decisions([{"ticker": "AAPL", "action": "SELL", "entry_price": 100.0}],
                  path=path, run_date="2026-06-01")
    _grade(path, "2026-07-01", returns={("AAPL", 5): 1.0}, complete={5})
    assert _records(path)[0]["checkpoints"]["5d"]["alpha_pct"] == 3.0


def test_decision_stays_open_until_its_horizon_lands(tmp_path):
    path = tmp_path / "d.jsonl"
    log_decisions([{"ticker": "AAPL", "action": "BUY", "entry_price": 100.0,
                    "target_horizon_days": 60}], path=path, run_date="2026-06-01")
    _grade(path, "2026-07-01", complete={5, 20})
    assert _records(path)[0]["status"] == "open"


def test_decision_becomes_graded_when_horizon_lands(tmp_path):
    path = tmp_path / "d.jsonl"
    log_decisions([{"ticker": "AAPL", "action": "BUY", "entry_price": 100.0,
                    "target_horizon_days": 7}], path=path, run_date="2026-06-01")
    # 7 calendar days -> 5 trading sessions
    _grade(path, "2026-07-01", complete={5})
    record = _records(path)[0]
    assert record["status"] == "graded"
    assert "horizon" in record["checkpoints"]


def test_horizon_converts_calendar_days_to_sessions(tmp_path):
    path = tmp_path / "d.jsonl"
    log_decisions([{"ticker": "AAPL", "action": "BUY", "entry_price": 100.0,
                    "target_horizon_days": 28}], path=path, run_date="2026-06-01")
    seen = []

    def window_fn(ticker, date, days):
        seen.append(days)
        return 10.0 if ticker != "SPY" else 4.0

    grade_open_decisions(path, window_fn=window_fn,
                         excursion_fn=lambda t, d, e: None, run_date="2026-07-01")
    assert 20 in seen  # 28 calendar days ~ 20 sessions, not 28


def test_checkpoints_are_write_once(tmp_path):
    path = tmp_path / "d.jsonl"
    log_decisions([{"ticker": "AAPL", "action": "BUY", "entry_price": 100.0}],
                  path=path, run_date="2026-06-01")
    _grade(path, "2026-07-01", complete={5})
    # a later run sees different prices; the elapsed window must not be revised
    _grade(path, "2026-07-02", returns={("AAPL", 5): 99.0}, complete={5})
    assert _records(path)[0]["checkpoints"]["5d"]["return_pct"] == 10.0


def test_later_windows_are_added_as_they_elapse(tmp_path):
    path = tmp_path / "d.jsonl"
    log_decisions([{"ticker": "AAPL", "action": "BUY", "entry_price": 100.0}],
                  path=path, run_date="2026-06-01")
    _grade(path, "2026-07-01", complete={5})
    _grade(path, "2026-08-01", complete={5, 20})
    assert set(_records(path)[0]["checkpoints"]) == {"5d", "20d"}


def test_excursion_tracks_both_directions(tmp_path):
    path = tmp_path / "d.jsonl"
    log_decisions([{"ticker": "AAPL", "action": "BUY", "entry_price": 100.0}],
                  path=path, run_date="2026-06-01")
    _grade(path, "2026-07-01", excursion=(18.4, -3.1), complete={5})
    assert _records(path)[0]["excursion"] == {"max_favorable_pct": 18.4,
                                              "max_adverse_pct": -3.1}


def test_excursion_updates_while_open(tmp_path):
    path = tmp_path / "d.jsonl"
    log_decisions([{"ticker": "AAPL", "action": "BUY", "entry_price": 100.0}],
                  path=path, run_date="2026-06-01")
    _grade(path, "2026-07-01", excursion=(5.0, -1.0), complete={5})
    _grade(path, "2026-07-02", excursion=(22.0, -1.0), complete={5})
    assert _records(path)[0]["excursion"]["max_favorable_pct"] == 22.0


def test_same_day_decisions_are_not_graded(tmp_path):
    path = tmp_path / "d.jsonl"
    log_decisions([{"ticker": "AAPL", "action": "BUY", "entry_price": 100.0}],
                  path=path, run_date="2026-06-10")
    assert _grade(path, "2026-06-10")["checkpoints"] == 0


def test_incomplete_windows_are_skipped_entirely(tmp_path):
    path = tmp_path / "d.jsonl"
    log_decisions([{"ticker": "AAPL", "action": "BUY", "entry_price": 100.0}],
                  path=path, run_date="2026-06-01")
    assert _grade(path, "2026-07-01", complete=set())["checkpoints"] == 0
    assert _records(path)[0]["status"] == "open"


def test_graded_decisions_are_not_regraded(tmp_path):
    path = tmp_path / "d.jsonl"
    log_decisions([{"ticker": "AAPL", "action": "BUY", "entry_price": 100.0,
                    "target_horizon_days": 7}], path=path, run_date="2026-06-01")
    _grade(path, "2026-07-01", complete={5})
    assert _grade(path, "2026-07-02", complete={5, 20})["checkpoints"] == 0


def test_legacy_pending_records_are_migrated(tmp_path):
    path = tmp_path / "d.jsonl"
    path.write_text(json.dumps({
        "ticker": "AAPL", "action": "BUY", "entry_price": 100.0,
        "date": "2026-06-01", "status": "pending"}) + "\n")
    _grade(path, "2026-07-01", complete={5})
    assert _records(path)[0]["status"] == "open"
    assert "5d" in _records(path)[0]["checkpoints"]


def test_grading_no_file(tmp_path):
    assert _grade(tmp_path / "nope.jsonl", "2026-06-10") == {"graded": 0,
                                                             "checkpoints": 0}


def test_grading_tolerates_corrupt_lines(tmp_path):
    path = tmp_path / "d.jsonl"
    path.write_text(json.dumps({"ticker": "AAPL", "action": "BUY",
                                "entry_price": 100.0, "date": "2026-06-01",
                                "status": "open"}) + "\nnot json\n")
    assert _grade(path, "2026-07-01", complete={5})["checkpoints"] == 1


# --- lessons ---

def test_best_checkpoint_prefers_the_longest_window():
    rec = {"checkpoints": {"5d": {"alpha_pct": 1.0}, "20d": {"alpha_pct": 2.0}}}
    assert best_checkpoint(rec)[0] == "20d"


def test_best_checkpoint_reads_legacy_outcome():
    assert best_checkpoint({"outcome": {"alpha_pct": 3.0}})[0] == "legacy"


def test_best_checkpoint_none_when_ungraded():
    assert best_checkpoint({"checkpoints": {}}) == (None, None)


def test_recent_lessons_summarizes_graded(tmp_path):
    path = tmp_path / "d.jsonl"
    log_decisions([{"ticker": "AAPL", "action": "BUY", "entry_price": 100.0}],
                  path=path, run_date="2026-06-01")
    _grade(path, "2026-07-01", complete={5})
    text = recent_lessons(path, "AAPL")
    assert "AAPL" in text and "alpha" in text.lower()


def test_recent_lessons_empty_when_no_file(tmp_path):
    assert recent_lessons(tmp_path / "none.jsonl", "AAPL") == ""


def test_recent_lessons_empty_when_nothing_graded(tmp_path):
    path = tmp_path / "d.jsonl"
    log_decisions([{"ticker": "AAPL", "action": "BUY"}], path=path,
                  run_date="2026-06-01")
    assert recent_lessons(path, "AAPL") == ""
