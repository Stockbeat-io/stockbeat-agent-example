from execution.validator import validate_buy, validate_sell

UNIVERSE = {"AAPL", "MSFT", "BRK-B"}
PORTFOLIO = {
    "total_equity": 100_000,
    "available_cash": 100_000,
    "holdings": {"MSFT": {"shares": 10}},
}

RISK_AGGRESSIVE = {"stance": "aggressive", "cash_target_pct": 10,
                   "default_position_pct": 18, "reasoning": "bull"}
RISK_DEFENSIVE = {"stance": "defensive", "cash_target_pct": 45,
                  "default_position_pct": 6, "reasoning": "bear"}


def _buy(**over):
    base = {"action": "BUY", "ticker": "AAPL", "usd_amount": 5_000,
            "target_price": 220.0, "target_horizon_days": 30,
            "why": "x" * 250}
    base.update(over)
    return base


def test_buy_skips_unknown_ticker():
    assert validate_buy(_buy(ticker="ZZZZ"), PORTFOLIO, UNIVERSE, 200.0) is None


def test_buy_normalizes_ticker():
    out = validate_buy(_buy(ticker="brk.b"), PORTFOLIO, UNIVERSE, 200.0)
    assert out["ticker"] == "BRK-B"


def test_buy_caps_position_to_default_10pct():
    out = validate_buy(_buy(usd_amount=50_000), PORTFOLIO, UNIVERSE, 200.0)
    # default position_pct 10% of 100k = 10_000
    assert out["usd_amount"] == 10_000


def test_buy_skips_below_min_trade():
    tiny = {"total_equity": 5_000, "available_cash": 5_000, "holdings": {}}
    # 5% of 5k = $250, below MIN_TRADE_USD (1000)
    out = validate_buy(_buy(position_pct=5), tiny, UNIVERSE, 200.0)
    assert out is None


def test_buy_clamps_target_price_to_max_upside():
    out = validate_buy(_buy(target_price=999.0), PORTFOLIO, UNIVERSE, 200.0)
    assert out["target_price"] == 500.0  # 2.5 * 200


def test_buy_fixes_target_price_below_entry():
    out = validate_buy(_buy(target_price=150.0), PORTFOLIO, UNIVERSE, 200.0)
    assert out["target_price"] == 210.0  # 1.05 * 200


def test_buy_clamps_horizon():
    out = validate_buy(_buy(target_horizon_days=500), PORTFOLIO, UNIVERSE, 200.0)
    assert out["target_horizon_days"] == 90


def test_buy_pads_short_why():
    out = validate_buy(_buy(why="too short"), PORTFOLIO, UNIVERSE, 200.0)
    assert 200 <= len(out["why"]) <= 400


def test_buy_truncates_long_why():
    out = validate_buy(_buy(why="y" * 800), PORTFOLIO, UNIVERSE, 200.0)
    assert len(out["why"]) == 400


def test_buy_truncates_at_word_boundary():
    text = "word " * 100  # 500 chars, spaces every 5 chars
    out = validate_buy(_buy(why=text), PORTFOLIO, UNIVERSE, 200.0)
    assert len(out["why"]) <= 400
    assert not out["why"].endswith(" ")
    assert " " not in out["why"][-1:]  # doesn't end mid-word


def test_buy_enforces_cash_reserve():
    # equity 100k, default cash_target 30% -> max_spend = 70k
    # default position 10% -> 10k (caps first)
    out = validate_buy(_buy(usd_amount=90_000), PORTFOLIO, UNIVERSE, 200.0)
    assert out["usd_amount"] == 10_000


def test_buy_uses_risk_assessment_position_pct():
    out = validate_buy(_buy(usd_amount=50_000), PORTFOLIO, UNIVERSE, 200.0,
                       risk_assessment=RISK_AGGRESSIVE)
    # 18% of 100k = 18_000
    assert out["usd_amount"] == 18_000


def test_buy_uses_risk_assessment_cash_target():
    # equity 100k, cash 100k, defensive cash_target 45% -> max spend = 100k - 45k = 55k
    out = validate_buy(_buy(usd_amount=60_000), PORTFOLIO, UNIVERSE, 200.0,
                       risk_assessment=RISK_DEFENSIVE)
    # position_pct 6% = 6k caps it first
    assert out["usd_amount"] == 6_000


def test_buy_clamps_out_of_range_position_pct():
    bad_risk = {"cash_target_pct": 20, "default_position_pct": 99}
    out = validate_buy(_buy(usd_amount=50_000), PORTFOLIO, UNIVERSE, 200.0,
                       risk_assessment=bad_risk)
    # clamped to POSITION_PCT_MAX (20%) = 20_000
    assert out["usd_amount"] == 20_000


def test_buy_clamps_out_of_range_cash_target():
    bad_risk = {"cash_target_pct": 0, "default_position_pct": 15}
    out = validate_buy(_buy(usd_amount=50_000), PORTFOLIO, UNIVERSE, 200.0,
                       risk_assessment=bad_risk)
    # cash_target clamped to MIN (10%), so max_spend = 100k - 10k = 90k
    # position 15% = 15k caps it
    assert out["usd_amount"] == 15_000


def test_buy_uses_per_trade_position_pct_override():
    out = validate_buy(_buy(usd_amount=50_000, position_pct=12), PORTFOLIO, UNIVERSE,
                       200.0, risk_assessment=RISK_AGGRESSIVE)
    # per-trade 12% overrides default 18%
    assert out["usd_amount"] == 12_000


def test_buy_defaults_when_risk_assessment_none():
    out = validate_buy(_buy(usd_amount=50_000), PORTFOLIO, UNIVERSE, 200.0,
                       risk_assessment=None)
    # defaults: position 10%, cash 30% -> position caps at 10k
    assert out["usd_amount"] == 10_000


def test_sell_skips_unheld():
    sell = {"action": "SELL", "ticker": "AAPL", "usd_amount": 1_000, "why": "z" * 250}
    assert validate_sell(sell, PORTFOLIO, UNIVERSE) is None


def test_sell_allows_held():
    sell = {"action": "SELL", "ticker": "MSFT", "usd_amount": 1_000, "why": "z" * 250}
    out = validate_sell(sell, PORTFOLIO, UNIVERSE)
    assert out["ticker"] == "MSFT"
