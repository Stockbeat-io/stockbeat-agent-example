"""Guards added after five weeks of live trading exposed three failure modes.

1. Cash reserve was validated against a stale snapshot, so a 10-action initial
   build spent 100% of equity despite a 20% cash target (trend-tess).
2. The prompt's illustrative `why` was copied verbatim into live rationales,
   including cloud-revenue reasoning for a chemicals company.
3. Positions were exited days after entry on a contradictory thesis, bleeding
   equity through round-trip churn (strict-stu).
"""

import json

import config
import main
from analysis.prompts import GOOD_WHY_EXAMPLE
from execution.validator import echoes_example, validate_buy, validate_sell

REAL_WHY = (
    "The recent pullback is a liquidity event rather than a change in the "
    "underlying earnings trajectory, and the order book has already stabilised. "
    "Sector peers re-rated first, leaving this name trading at a discount that "
    "should close as the next quarter confirms the trend."
)


def _portfolio(cash=100_000, equity=100_000, holdings=None):
    return {"total_equity": equity, "available_cash": cash,
            "holdings": holdings if holdings is not None else {}}


# --- 1. cash reserve is enforced across a whole run ---

def test_single_buy_respects_the_cash_floor():
    action = {"action": "BUY", "ticker": "AAPL", "position_pct": 10, "why": REAL_WHY}
    valid = validate_buy(action, _portfolio(), {"AAPL"}, 100.0,
                         risk_assessment={"cash_target_pct": 20})
    assert valid["usd_amount"] == 10_000


def test_buy_is_capped_by_remaining_headroom():
    # only $5k of spendable cash left above a 20% floor
    valid = validate_buy(
        {"action": "BUY", "ticker": "AAPL", "position_pct": 10, "why": REAL_WHY},
        _portfolio(cash=25_000), {"AAPL"}, 100.0,
        risk_assessment={"cash_target_pct": 20})
    assert valid["usd_amount"] == 5_000


def test_buy_rejected_when_floor_already_reached():
    assert validate_buy(
        {"action": "BUY", "ticker": "AAPL", "position_pct": 10, "why": REAL_WHY},
        _portfolio(cash=20_000), {"AAPL"}, 100.0,
        risk_assessment={"cash_target_pct": 20}) is None


def test_initial_build_leaves_the_cash_reserve_intact(monkeypatch, tmp_path):
    """The trend-tess regression: 10 buys must not consume 100% of equity."""
    from tests.test_main import FakeClient, FakeLLM, OPEN, _patch_data

    _patch_data(monkeypatch)
    monkeypatch.setattr(main, "_memory_path", lambda: tmp_path / "d.jsonl")
    universe = set(config.SKELETON_TICKERS)
    client = FakeClient(
        {"total_equity": 100_000, "available_cash": 100_000, "holdings": {},
         "trade_tokens": 20}, universe)
    actions = [{"action": "BUY", "ticker": t, "position_pct": 10,
                "target_price": 400.0, "target_horizon_days": 60, "why": REAL_WHY}
               for t in universe]
    llm = FakeLLM(json.dumps({
        "risk_assessment": {"stance": "neutral", "cash_target_pct": 20,
                            "default_position_pct": 10},
        "actions": actions}))

    main.run(now_et=OPEN, client=client, llm=llm)

    logged = [json.loads(line) for line in
              (tmp_path / "d.jsonl").read_text().splitlines() if line.strip()]
    spent = sum(r.get("usd_amount") or 0 for r in logged if r["action"] == "BUY")
    assert spent <= 80_000, f"spent {spent}, breaching the 20% cash floor"
    assert spent > 0, "expected the build to actually buy something"


# --- 2. the prompt's example must not become a trade rationale ---

def test_verbatim_example_is_detected():
    assert echoes_example(GOOD_WHY_EXAMPLE)


def test_example_embedded_in_longer_text_is_detected():
    assert echoes_example("Buying here. " + GOOD_WHY_EXAMPLE + " Strong setup.")


def test_example_detection_ignores_case_and_punctuation():
    assert echoes_example(GOOD_WHY_EXAMPLE.upper().replace(",", ""))


def test_genuine_rationale_is_not_flagged():
    assert not echoes_example(REAL_WHY)


def test_short_incidental_overlap_is_not_flagged():
    assert not echoes_example("Margin expansion from mix shift is already priced in here.")


def test_empty_why_is_not_flagged():
    assert not echoes_example("") and not echoes_example(None)


def test_buy_with_copied_rationale_is_dropped():
    assert validate_buy(
        {"action": "BUY", "ticker": "AAPL", "position_pct": 10,
         "why": GOOD_WHY_EXAMPLE}, _portfolio(), {"AAPL"}, 100.0) is None


def test_sell_with_copied_rationale_is_dropped():
    assert validate_sell(
        {"action": "SELL", "ticker": "AAPL", "why": GOOD_WHY_EXAMPLE},
        _portfolio(holdings={"AAPL": {}}), {"AAPL"}) is None


def test_buy_with_genuine_rationale_survives():
    assert validate_buy(
        {"action": "BUY", "ticker": "AAPL", "position_pct": 10, "why": REAL_WHY},
        _portfolio(), {"AAPL"}, 100.0) is not None


# --- 3. minimum holding period ---

def test_sell_blocked_inside_the_holding_period():
    assert validate_sell(
        {"action": "SELL", "ticker": "AAPL", "why": REAL_WHY},
        _portfolio(holdings={"AAPL": {}}), {"AAPL"},
        held_days=1, min_holding_days=15) is None


def test_sell_allowed_once_the_period_has_passed():
    assert validate_sell(
        {"action": "SELL", "ticker": "AAPL", "why": REAL_WHY},
        _portfolio(holdings={"AAPL": {}}), {"AAPL"},
        held_days=20, min_holding_days=15) is not None


def test_holding_period_boundary_is_inclusive():
    assert validate_sell(
        {"action": "SELL", "ticker": "AAPL", "why": REAL_WHY},
        _portfolio(holdings={"AAPL": {}}), {"AAPL"},
        held_days=15, min_holding_days=15) is not None


def test_unknown_holding_age_does_not_block_the_sell():
    # a position with no logged entry must remain exitable
    assert validate_sell(
        {"action": "SELL", "ticker": "AAPL", "why": REAL_WHY},
        _portfolio(holdings={"AAPL": {}}), {"AAPL"},
        held_days=None, min_holding_days=15) is not None


def test_holding_period_defaults_to_config(monkeypatch):
    monkeypatch.setattr(config, "MIN_HOLDING_DAYS", 10)
    assert validate_sell(
        {"action": "SELL", "ticker": "AAPL", "why": REAL_WHY},
        _portfolio(holdings={"AAPL": {}}), {"AAPL"}, held_days=2) is None


def test_held_days_are_measured_from_the_logged_entry(tmp_path):
    path = tmp_path / "d.jsonl"
    path.write_text(json.dumps({"action": "BUY", "ticker": "AAPL",
                                "date": "2026-07-01"}) + "\n")
    assert main._held_days(path, "2026-07-15")["AAPL"] == 14


def test_held_days_ignores_closed_positions(tmp_path):
    path = tmp_path / "d.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in [
        {"action": "BUY", "ticker": "AAPL", "date": "2026-07-01"},
        {"action": "SELL", "ticker": "AAPL", "date": "2026-07-05"},
    ]) + "\n")
    assert "AAPL" not in main._held_days(path, "2026-07-15")


def test_held_days_tolerates_a_bad_run_date(tmp_path):
    assert main._held_days(tmp_path / "none.jsonl", "not-a-date") == {}


def test_profiles_carry_a_holding_floor_matching_their_horizon():
    from profiles import load_profile
    # a swing trader stays nimble; a 60-90 day investor cannot flip overnight
    assert load_profile("swing-sue")["risk_overrides"]["min_holding_days"] == 2
    assert load_profile("strict-stu")["risk_overrides"]["min_holding_days"] == 15
