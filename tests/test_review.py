import json

from memory import review


def _agent_log(root, agent, records):
    path = root / agent / "memory" / "decisions.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")
    return path


def _buy(ticker, alpha, checkpoint="20d", **extra):
    rec = {"ticker": ticker, "action": "BUY", "date": "2026-07-01",
           "status": "graded",
           "checkpoints": {checkpoint: {"return_pct": alpha + 1.0,
                                        "spy_return_pct": 1.0,
                                        "alpha_pct": alpha}}}
    rec.update(extra)
    return rec


# --- loading ---

def test_load_decisions_tags_each_record_with_its_agent(tmp_path):
    _agent_log(tmp_path, "momentum-mike", [_buy("AAPL", 2.0)])
    _agent_log(tmp_path, "value-vicky", [_buy("MSFT", 1.0)])
    records = review.load_decisions(tmp_path)
    assert {r["agent"] for r in records} == {"momentum-mike", "value-vicky"}


def test_load_decisions_ignores_dirs_without_logs(tmp_path):
    (tmp_path / "empty").mkdir()
    _agent_log(tmp_path, "momentum-mike", [_buy("AAPL", 2.0)])
    assert len(review.load_decisions(tmp_path)) == 1


def test_load_decisions_skips_corrupt_lines(tmp_path):
    path = _agent_log(tmp_path, "a", [_buy("AAPL", 2.0)])
    path.write_text(path.read_text() + "not json\n")
    assert len(review.load_decisions(tmp_path)) == 1


def test_load_decisions_empty_root(tmp_path):
    assert review.load_decisions(tmp_path / "nothing") == []


# --- statistics ---

def test_summarize_reports_mean_win_rate_and_interval():
    stats = review.summarize([2.0, -1.0, 4.0, 3.0])
    assert stats["n"] == 4
    assert stats["mean"] == 2.0
    assert stats["win_pct"] == 75.0
    assert stats["ci"] > 0


def test_summarize_handles_empty():
    assert review.summarize([]) == {"n": 0, "mean": None, "win_pct": None, "ci": None}


def test_confidence_interval_narrows_with_more_samples():
    few = review.summarize([2.0, -1.0, 4.0, 3.0])
    many = review.summarize([2.0, -1.0, 4.0, 3.0] * 25)
    assert many["ci"] < few["ci"]


def test_alpha_at_returns_none_for_unlanded_window():
    assert review.alpha_at(_buy("AAPL", 2.0, checkpoint="5d"), "20d") is None


# --- the statistical guard ---

def test_small_samples_are_labelled_insufficient(tmp_path):
    _agent_log(tmp_path, "momentum-mike", [_buy(f"T{i}", 5.0) for i in range(4)])
    out = review.render(review.load_decisions(tmp_path))
    assert "insufficient" in out


def test_all_agents_underpowered_gets_an_explicit_warning(tmp_path):
    _agent_log(tmp_path, "momentum-mike", [_buy(f"T{i}", 5.0) for i in range(4)])
    _agent_log(tmp_path, "buzz-bo", [_buy(f"U{i}", -5.0) for i in range(4)])
    out = review.render(review.load_decisions(tmp_path))
    assert "not" in out and "chance" in out


def test_large_samples_are_not_labelled_insufficient(tmp_path):
    _agent_log(tmp_path, "momentum-mike",
               [_buy(f"T{i}", 5.0) for i in range(review.MIN_SAMPLES + 1)])
    out = review.render(review.load_decisions(tmp_path))
    assert "insufficient" not in out
    assert "do not act on it" not in out


def test_sample_size_is_always_shown(tmp_path):
    _agent_log(tmp_path, "momentum-mike", [_buy(f"T{i}", 5.0) for i in range(3)])
    out = review.render(review.load_decisions(tmp_path))
    assert "3" in out and "±" in out


# --- grouping ---

def test_by_agent_groups_alphas(tmp_path):
    _agent_log(tmp_path, "a", [_buy("AAPL", 2.0), _buy("MSFT", 4.0)])
    _agent_log(tmp_path, "b", [_buy("NVDA", -2.0)])
    grouped = review.by_agent(review.load_decisions(tmp_path), "20d")
    assert grouped["a"]["mean"] == 3.0
    assert grouped["b"]["mean"] == -2.0


def test_by_feature_buckets_numeric_values(tmp_path):
    records = [
        _buy("AAPL", 4.0, features={"position_pct": 10}),
        _buy("MSFT", -2.0, features={"position_pct": 15}),
    ]
    _agent_log(tmp_path, "a", records)
    grouped = review.by_feature(review.load_decisions(tmp_path), "20d",
                                "position_pct", [(9, "<9%"), (11, "9-11%"),
                                                 (13, "11-13%"), (99, ">=13%")])
    assert grouped["9-11%"]["mean"] == 4.0
    assert grouped[">=13%"]["mean"] == -2.0


def test_by_feature_groups_strings_directly(tmp_path):
    _agent_log(tmp_path, "a", [_buy("AAPL", 4.0, features={"sector": "Tech"})])
    grouped = review.by_feature(review.load_decisions(tmp_path), "20d", "sector", [])
    assert grouped["Tech"]["mean"] == 4.0


def test_by_feature_skips_records_without_the_feature(tmp_path):
    _agent_log(tmp_path, "a", [_buy("AAPL", 4.0), _buy("MSFT", 1.0, features={})])
    assert review.by_feature(review.load_decisions(tmp_path), "20d", "rsi", []) == {}


# --- rendering ---

def test_render_separates_buys_from_exits(tmp_path):
    sell = _buy("MSFT", 1.0)
    sell["action"] = "SELL"
    _agent_log(tmp_path, "a", [_buy("AAPL", 2.0), sell])
    out = review.render(review.load_decisions(tmp_path))
    assert "BUY" in out and "SELL/EXIT" in out


def test_render_reports_graded_counts(tmp_path):
    _agent_log(tmp_path, "a", [_buy("AAPL", 2.0, checkpoint="5d")])
    out = review.render(review.load_decisions(tmp_path), checkpoint="20d")
    assert "0 graded" in out


def test_main_returns_error_without_logs(tmp_path, capsys):
    assert review.main(["--root", str(tmp_path / "none")]) == 1
    assert "No decision logs" in capsys.readouterr().out


def test_main_prints_report(tmp_path, capsys):
    _agent_log(tmp_path, "a", [_buy("AAPL", 2.0)])
    assert review.main(["--root", str(tmp_path)]) == 0
    assert "StockBeat agent review" in capsys.readouterr().out
