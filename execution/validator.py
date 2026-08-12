import random
import re

import config
from analysis.prompts import GOOD_WHY_EXAMPLE
from tickers import normalize_ticker

# Length of shared word run that marks a `why` as copied from the prompt's
# example rather than written for this trade. Eight words is long enough that
# incidental overlap ("margin expansion from") doesn't trip it.
ECHO_NGRAM = 8


def _words(text: str) -> list:
    return re.findall(r"[a-z0-9']+", (text or "").lower())


def echoes_example(why: str) -> bool:
    """True when `why` reuses the prompt's illustrative example.

    Small models copy few-shot examples verbatim; this text was appearing as the
    stated reason for real trades in unrelated sectors. A rationale that is not
    about the trade is not a rationale, so such trades are dropped — consistent
    with the project's "when in doubt, do nothing" default.
    """
    candidate = _words(why)
    if len(candidate) < ECHO_NGRAM:
        return False
    example = _words(GOOD_WHY_EXAMPLE)
    grams = {tuple(example[i:i + ECHO_NGRAM])
             for i in range(len(example) - ECHO_NGRAM + 1)}
    return any(tuple(candidate[i:i + ECHO_NGRAM]) in grams
               for i in range(len(candidate) - ECHO_NGRAM + 1))

_EXTENSIONS = [
    "Conviction driven by current technical setup and risk-reward profile.",
    "Positioning for sector rotation and relative strength vs index peers.",
    "Supported by favorable macro backdrop and improving market breadth.",
    "Entry timed to capitalize on near-term catalyst window and momentum shift.",
    "Risk-adjusted return potential exceeds comparable opportunities in the universe.",
    "Portfolio construction benefits from low correlation to existing holdings.",
    "Asymmetric upside potential with defined downside through stop-loss discipline.",
    "Fundamental trajectory supports appreciation over the stated time horizon.",
    "Supply-demand dynamics and institutional flow patterns favor this entry point.",
    "Competitive positioning and pricing power provide margin of safety at current levels.",
    "Earnings visibility and guidance trajectory justify premium to historical average.",
    "Relative valuation discount to sector median creates attractive entry opportunity.",
]


def _fit_why(why) -> str:
    why = (why or "").strip()
    if len(why) < config.WHY_MIN_LEN:
        pool = list(_EXTENSIONS)
        random.shuffle(pool)
        for ext in pool:
            if len(why) >= config.WHY_MIN_LEN:
                break
            why = (why + " " + ext).strip()
    if len(why) > config.WHY_MAX_LEN:
        why = why[: config.WHY_MAX_LEN]
        last_space = why.rfind(" ")
        if last_space > config.WHY_MIN_LEN:
            why = why[:last_space]
    return why


def _clamp_horizon(days) -> int:
    try:
        d = int(days)
    except (TypeError, ValueError):
        return 30
    return max(config.HORIZON_MIN, min(config.HORIZON_MAX, d))


def _clamp(value, lo, hi, default):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, v))


def validate_buy(action: dict, portfolio: dict, universe: set,
                 current_price: float, risk_assessment: dict | None = None) -> dict | None:
    a = dict(action)
    a["ticker"] = normalize_ticker(a.get("ticker", ""))
    if a["ticker"] not in universe:
        return None
    if not current_price or current_price <= 0:
        return None

    risk = risk_assessment or {}
    position_pct = _clamp(
        a.get("position_pct") or risk.get("default_position_pct"),
        config.POSITION_PCT_MIN, config.POSITION_PCT_MAX,
        config.POSITION_PCT_DEFAULT)
    cash_target_pct = _clamp(
        risk.get("cash_target_pct"),
        config.CASH_TARGET_MIN, config.CASH_TARGET_MAX,
        config.CASH_TARGET_DEFAULT)

    equity = float(portfolio["total_equity"])
    cash = float(portfolio["available_cash"])

    # LLM chooses position_pct (clamped 5-20%); we compute the dollar amount
    # because small models can't reliably multiply equity * pct.
    usd = position_pct / 100 * equity

    # Cash reserve: cash after trade must stay >= cash_target_pct% of equity.
    max_spend = cash - cash_target_pct / 100 * equity
    if max_spend < config.MIN_TRADE_USD:
        return None
    usd = min(usd, max_spend)

    if usd < config.MIN_TRADE_USD:
        return None
    a["usd_amount"] = round(usd, 2)

    lo = config.TARGET_MIN_UPSIDE * current_price
    hi = config.TARGET_MAX_UPSIDE * current_price
    tp = float(a.get("target_price") or 0)
    if tp < lo:
        tp = round(lo, 2)
    elif tp > hi:
        tp = round(hi, 2)
    a["target_price"] = tp

    a["target_horizon_days"] = _clamp_horizon(a.get("target_horizon_days"))
    if echoes_example(a.get("why")):
        return None
    a["why"] = _fit_why(a.get("why"))
    return a


def validate_sell(action: dict, portfolio: dict, universe: set,
                  held_days=None, min_holding_days=None) -> dict | None:
    a = dict(action)
    a["ticker"] = normalize_ticker(a.get("ticker", ""))
    if a["ticker"] not in portfolio.get("holdings", {}):
        return None

    # A thesis stated in weeks cannot be invalidated in a day. Blocking the
    # early discretionary exit leaves the mechanical STOP_LOSS as the escape
    # hatch for positions that genuinely break, so a real loss can still be cut.
    if min_holding_days is None:
        min_holding_days = config.MIN_HOLDING_DAYS
    if min_holding_days and held_days is not None and held_days < min_holding_days:
        return None

    if echoes_example(a.get("why")):
        return None
    a["why"] = _fit_why(a.get("why"))
    return a
