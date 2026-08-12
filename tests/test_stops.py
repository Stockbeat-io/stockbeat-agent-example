import json

import pytest

from execution import stops


class FakeClient:
    """Records submitted trades; mimics the MCP client's return shape."""

    def __init__(self, fail_tickers=()):
        self.calls = []
        self.fail_tickers = set(fail_tickers)

    def submit_trade(self, action, ticker, **kwargs):
        self.calls.append({"action": action, "ticker": ticker, **kwargs})
        if ticker in self.fail_tickers:
            return {"status": "error", "error_code": "ERR_TEST"}
        return {"status": "ok"}


def _manage(client, holdings, pending=(), decisions_path=None, prices=None,
            highs=None, stop_pct=0.06, tmp_path=None):
    """Call manage_stop_losses with injected data sources."""
    prices = prices or {}
    highs = highs or {}
    path = decisions_path if decisions_path is not None else (tmp_path / "none.jsonl")
    return stops.manage_stop_losses(
        client, holdings, list(pending), path,
        price_fn=lambda t: prices.get(t),
        high_water_fn=lambda t, since: highs.get(t),
        stop_pct=stop_pct,
    )


# --- target_stop_price: the three gain bands ---

def test_below_breakeven_band_stop_sits_under_entry():
    # +3% gain -> unchanged behaviour, stop stays stop_pct below entry
    assert stops.target_stop_price(100.0, 103.0, 103.0, 0.06) == pytest.approx(94.0)


def test_breakeven_band_ratchets_stop_to_entry():
    # +10% gain is past BREAKEVEN_GAIN_PCT (8%) but below TRAIL_GAIN_PCT (15%)
    assert stops.target_stop_price(100.0, 110.0, 112.0, 0.06) == pytest.approx(100.0)


def test_trail_band_trails_the_high_water_mark():
    # +20% gain -> trail 6% below the high-water mark (120), not below entry
    assert stops.target_stop_price(100.0, 120.0, 125.0, 0.06) == pytest.approx(117.5)


def test_band_boundaries_are_inclusive():
    assert stops.target_stop_price(100.0, 108.0, 108.0, 0.06) == pytest.approx(100.0)
    assert stops.target_stop_price(100.0, 115.0, 115.0, 0.06) == pytest.approx(108.1)


def test_trail_band_without_high_water_falls_back_to_current_price():
    assert stops.target_stop_price(100.0, 120.0, None, 0.06) == pytest.approx(112.8)


def test_stop_pct_is_honoured_per_agent():
    # a wider-stop agent (Value Vicky, 8%) trails further back
    assert stops.target_stop_price(100.0, 120.0, 125.0, 0.08) == pytest.approx(115.0)


@pytest.mark.parametrize("avg,price", [(0, 100.0), (None, 100.0), (100.0, None)])
def test_unpriceable_positions_yield_no_stop(avg, price):
    assert stops.target_stop_price(avg, price, 120.0, 0.06) is None


# --- manage_stop_losses: placing ---

def test_places_stop_when_position_has_none(tmp_path):
    client = FakeClient()
    result = _manage(client, {"AAPL": {"avg_price": 100.0}},
                     prices={"AAPL": 103.0}, tmp_path=tmp_path)
    assert result == {"placed": 1, "raised": 0}
    assert client.calls[0]["action"] == "STOP_LOSS"
    assert client.calls[0]["limit_price"] == pytest.approx(94.0)


def test_stop_why_meets_api_length_rules(tmp_path):
    client = FakeClient()
    _manage(client, {"AAPL": {"avg_price": 100.0}},
            prices={"AAPL": 103.0}, tmp_path=tmp_path)
    why = client.calls[0]["why"]
    assert 200 <= len(why) <= 400


def test_skips_position_with_no_price(tmp_path):
    client = FakeClient()
    result = _manage(client, {"AAPL": {"avg_price": 100.0}}, prices={},
                     tmp_path=tmp_path)
    assert result == {"placed": 0, "raised": 0}
    assert client.calls == []


def test_falls_back_to_entry_price_key(tmp_path):
    # the REST portfolio uses avg_price; older records carry entry_price
    client = FakeClient()
    result = _manage(client, {"AAPL": {"entry_price": 100.0}},
                     prices={"AAPL": 103.0}, tmp_path=tmp_path)
    assert result["placed"] == 1


def test_failed_submission_is_not_counted(tmp_path):
    client = FakeClient(fail_tickers={"AAPL"})
    result = _manage(client, {"AAPL": {"avg_price": 100.0}},
                     prices={"AAPL": 103.0}, tmp_path=tmp_path)
    assert result == {"placed": 0, "raised": 0}


# --- manage_stop_losses: ratcheting ---

def test_raises_existing_stop_when_position_runs_up(tmp_path):
    # bought at 100, now 120, old stop still at 94 -> trail to 6% under high-water 125
    client = FakeClient()
    pending = [{"action": "STOP_LOSS", "ticker": "AAPL", "limit_price": 94.0}]
    result = _manage(client, {"AAPL": {"avg_price": 100.0}}, pending=pending,
                     prices={"AAPL": 120.0}, highs={"AAPL": 125.0}, tmp_path=tmp_path)
    assert result == {"placed": 0, "raised": 1}
    assert client.calls[0]["limit_price"] == pytest.approx(117.5)


def test_never_loosens_an_existing_stop(tmp_path):
    # still in the trail band, but the stop already in place is tighter than the
    # one the rule computes (117.5) — a ratchet must never run backwards
    client = FakeClient()
    pending = [{"action": "STOP_LOSS", "ticker": "AAPL", "limit_price": 118.0}]
    result = _manage(client, {"AAPL": {"avg_price": 100.0}}, pending=pending,
                     prices={"AAPL": 120.0}, highs={"AAPL": 125.0}, tmp_path=tmp_path)
    assert result == {"placed": 0, "raised": 0}
    assert client.calls == []


def test_does_not_touch_existing_stop_on_a_losing_position(tmp_path):
    # tightening a loser's stop would exit it near the low; leave the Judge's
    # stop alone until there is actually a gain to protect
    client = FakeClient()
    pending = [{"action": "STOP_LOSS", "ticker": "AAPL", "limit_price": 88.0}]
    result = _manage(client, {"AAPL": {"avg_price": 100.0}}, pending=pending,
                     prices={"AAPL": 95.0}, tmp_path=tmp_path)
    assert result == {"placed": 0, "raised": 0}
    assert client.calls == []


def test_does_not_touch_existing_stop_below_the_breakeven_band(tmp_path):
    client = FakeClient()
    pending = [{"action": "STOP_LOSS", "ticker": "AAPL", "limit_price": 88.0}]
    result = _manage(client, {"AAPL": {"avg_price": 100.0}}, pending=pending,
                     prices={"AAPL": 107.0}, tmp_path=tmp_path)
    assert result == {"placed": 0, "raised": 0}


def test_still_places_a_missing_stop_on_a_losing_position(tmp_path):
    # an unprotected position must always get a stop, gain or no gain
    client = FakeClient()
    result = _manage(client, {"AAPL": {"avg_price": 100.0}},
                     prices={"AAPL": 95.0}, tmp_path=tmp_path)
    assert result == {"placed": 1, "raised": 0}


def test_ratchets_once_the_breakeven_band_is_reached(tmp_path):
    client = FakeClient()
    pending = [{"action": "STOP_LOSS", "ticker": "AAPL", "limit_price": 88.0}]
    result = _manage(client, {"AAPL": {"avg_price": 100.0}}, pending=pending,
                     prices={"AAPL": 108.0}, tmp_path=tmp_path)
    assert result == {"placed": 0, "raised": 1}
    assert client.calls[0]["limit_price"] == pytest.approx(100.0)


def test_ignores_change_below_churn_threshold(tmp_path):
    # a 0.1% raise is not worth an API call
    client = FakeClient()
    pending = [{"action": "STOP_LOSS", "ticker": "AAPL", "limit_price": 99.9}]
    result = _manage(client, {"AAPL": {"avg_price": 100.0}}, pending=pending,
                     prices={"AAPL": 110.0}, tmp_path=tmp_path)
    assert result == {"placed": 0, "raised": 0}


def test_never_places_stop_at_or_above_market(tmp_path):
    # a stop above the current price would fire instantly
    client = FakeClient()
    result = _manage(client, {"AAPL": {"avg_price": 100.0}},
                     prices={"AAPL": 120.0}, highs={"AAPL": 400.0},
                     tmp_path=tmp_path)
    assert result == {"placed": 0, "raised": 0}
    assert client.calls == []


def test_high_water_is_at_least_the_current_price(tmp_path):
    # stale/missing history must not produce a looser stop than today's price implies
    client = FakeClient()
    result = _manage(client, {"AAPL": {"avg_price": 100.0}},
                     prices={"AAPL": 120.0}, highs={"AAPL": 110.0}, tmp_path=tmp_path)
    assert client.calls[0]["limit_price"] == pytest.approx(112.8)


def test_only_stop_loss_orders_count_as_existing_cover(tmp_path):
    client = FakeClient()
    pending = [{"action": "BUY_LIMIT", "ticker": "AAPL", "limit_price": 90.0}]
    result = _manage(client, {"AAPL": {"avg_price": 100.0}}, pending=pending,
                     prices={"AAPL": 103.0}, tmp_path=tmp_path)
    assert result["placed"] == 1


def test_handles_many_positions_independently(tmp_path):
    client = FakeClient()
    holdings = {"AAPL": {"avg_price": 100.0}, "MSFT": {"avg_price": 200.0}}
    pending = [{"action": "STOP_LOSS", "ticker": "MSFT", "limit_price": 188.0}]
    result = _manage(client, holdings, pending=pending,
                     prices={"AAPL": 101.0, "MSFT": 240.0},
                     highs={"MSFT": 250.0}, tmp_path=tmp_path)
    assert result == {"placed": 1, "raised": 1}


def test_uses_logged_entry_price_when_portfolio_omits_avg_price(tmp_path):
    # a position with no stop is the worst outcome, so fall back to the log
    path = tmp_path / "d.jsonl"
    _write(path, [{"action": "BUY", "ticker": "AAPL", "date": "2026-07-01",
                   "entry_price": 100.0}])
    client = FakeClient()
    result = _manage(client, {"AAPL": {"shares": 10}}, decisions_path=path,
                     prices={"AAPL": 103.0}, tmp_path=tmp_path)
    assert result["placed"] == 1
    assert client.calls[0]["limit_price"] == pytest.approx(94.0)


def test_falls_back_to_the_judges_stop_when_no_entry_is_known(tmp_path):
    # neither the portfolio nor the log carries an entry price, but the Judge
    # picked a stop when it opened the trade — honour it rather than skip
    path = tmp_path / "d.jsonl"
    _write(path, [{"action": "BUY", "ticker": "AAPL", "date": "2026-07-01",
                   "stop_loss_price": 300.0}])
    client = FakeClient()
    result = _manage(client, {"AAPL": {"shares": 10}}, decisions_path=path,
                     prices={"AAPL": 319.0}, tmp_path=tmp_path)
    assert result["placed"] == 1
    assert client.calls[0]["limit_price"] == pytest.approx(300.0)


def test_skips_when_no_entry_and_no_judge_stop(tmp_path):
    path = tmp_path / "d.jsonl"
    _write(path, [{"action": "BUY", "ticker": "AAPL", "date": "2026-07-01"}])
    client = FakeClient()
    result = _manage(client, {"AAPL": {"shares": 10}}, decisions_path=path,
                     prices={"AAPL": 319.0}, tmp_path=tmp_path)
    assert result == {"placed": 0, "raised": 0}


# --- position_context ---

def _write(path, records):
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")


def test_open_date_is_first_buy(tmp_path):
    path = tmp_path / "d.jsonl"
    _write(path, [
        {"action": "BUY", "ticker": "AAPL", "date": "2026-07-10"},
        {"action": "BUY", "ticker": "AAPL", "date": "2026-07-20"},
    ])
    assert stops.position_context(path)["AAPL"]["open_date"] == "2026-07-10"


def test_context_carries_entry_price(tmp_path):
    path = tmp_path / "d.jsonl"
    _write(path, [{"action": "BUY", "ticker": "AAPL", "date": "2026-07-10",
                   "entry_price": 123.45}])
    assert stops.position_context(path)["AAPL"]["entry_price"] == 123.45


def test_exit_resets_the_open_date(tmp_path):
    path = tmp_path / "d.jsonl"
    _write(path, [
        {"action": "BUY", "ticker": "AAPL", "date": "2026-07-01"},
        {"action": "SELL", "ticker": "AAPL", "date": "2026-07-05"},
        {"action": "BUY", "ticker": "AAPL", "date": "2026-07-10"},
    ])
    assert stops.position_context(path)["AAPL"]["open_date"] == "2026-07-10"


def test_close_all_clears_every_position(tmp_path):
    path = tmp_path / "d.jsonl"
    _write(path, [
        {"action": "BUY", "ticker": "AAPL", "date": "2026-07-01"},
        {"action": "BUY", "ticker": "MSFT", "date": "2026-07-02"},
        {"action": "CLOSE_ALL", "date": "2026-07-03"},
    ])
    assert stops.position_context(path) == {}


def test_records_are_ordered_by_date_not_file_order(tmp_path):
    path = tmp_path / "d.jsonl"
    _write(path, [
        {"action": "BUY", "ticker": "AAPL", "date": "2026-07-20"},
        {"action": "BUY", "ticker": "AAPL", "date": "2026-07-10"},
    ])
    assert stops.position_context(path)["AAPL"]["open_date"] == "2026-07-10"


def test_context_tolerates_missing_file_and_bad_lines(tmp_path):
    assert stops.position_context(tmp_path / "nope.jsonl") == {}
    path = tmp_path / "d.jsonl"
    path.write_text('{"action": "BUY", "ticker": "A", "date": "2026-07-01"}\nnot json\n\n')
    assert stops.position_context(path) == {
        "A": {"open_date": "2026-07-01", "entry_price": None,
              "stop_loss_price": None}}
