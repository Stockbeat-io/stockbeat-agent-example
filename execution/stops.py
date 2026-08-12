"""Trailing stop-loss management.

Stop-loss updates are free on StockBeat — the backend consumes no trade token
for them (``tradeService.js``: "Stop-loss updates are free"), and re-sending a
STOP_LOSS for a ticker that already has a pending one re-prices it rather than
rejecting it. So every holding gets its stop re-evaluated on every run,
independently of the LLM pipeline and of the trade-token budget.

The rule is a one-way ratchet in three bands, keyed on unrealized gain:

    gain < 8%    stop sits ``stop_pct`` below entry   (the original behaviour)
    gain >= 8%   stop moves up to breakeven
    gain >= 15%  stop trails ``stop_pct`` below the high-water mark

A stop is never loosened, and a change smaller than ``MIN_CHANGE_PCT`` is not
worth an API call.
"""

import json
from pathlib import Path

import config
from config import get_logger

log = get_logger()

BREAKEVEN_GAIN_PCT = 0.08   # at +8% unrealized, the stop ratchets up to entry
TRAIL_GAIN_PCT = 0.15       # at +15%, the stop trails the high-water mark
MIN_CHANGE_PCT = 0.005      # don't re-submit for a move smaller than 0.5%

# Band edges are exact percentages, but the gains compared against them come out
# of division (115/100 - 1 == 0.1499...), so the comparison needs a tolerance or
# a position sitting exactly on a boundary lands in the wrong band.
_EPS = 1e-9

# Anything that can empty a position, and so ends the holding period used for
# the high-water mark.
_EXIT_ACTIONS = {"SELL", "CLOSE_STOCK", "STOP_LOSS"}

_STOP_LOSS_REASONS = [
    "Protects capital by capping maximum drawdown at a predefined level, freeing funds for higher-conviction entries when conditions improve.",
    "Sets a hard floor beneath the position to enforce disciplined risk management and prevent emotional decision-making during volatile sessions.",
    "Limits portfolio-level impact of a single adverse move, preserving dry powder for reallocation to stronger setups in the watchlist.",
    "Defines the maximum acceptable loss for this position based on recent volatility and support structure, ensuring risk stays proportional to conviction.",
    "Guards against gap-down scenarios by automating the exit, removing latency between signal and action during fast-moving selloffs.",
    "Anchors downside to a technical level where the original thesis is invalidated, preventing small losses from compounding into significant drawdowns.",
]


def stop_loss_why(ticker: str, stop_price=None, entry_price=None) -> str:
    """Build a `why` string that satisfies the API's 200-400 character rule."""
    import random

    base = random.choice(_STOP_LOSS_REASONS)
    if stop_price and entry_price and entry_price > 0:
        pct = round((1 - stop_price / entry_price) * 100, 1)
        if pct < 0:
            base = f"Trailing stop raised to ${stop_price} for {ticker}, {abs(pct)}% above entry to lock in gains. {base}"
        else:
            base = f"Stop-loss at {pct}% below entry for {ticker}. {base}"
    else:
        base = f"Stop-loss for {ticker}. {base}"
    from execution.validator import _fit_why
    return _fit_why(base)


def target_stop_price(avg_price, current_price, high_water, stop_pct):
    """Return the stop price a position should carry, or None if unpriceable.

    Pure function — the band thresholds live here so they can be tested without
    any client, price feed, or decision log.
    """
    if not avg_price or avg_price <= 0 or not current_price:
        return None
    gain = current_price / avg_price - 1
    if gain >= TRAIL_GAIN_PCT - _EPS:
        # A stale or missing high-water mark must never produce a looser stop
        # than today's price already justifies.
        peak = max(high_water or 0, current_price)
        return peak * (1 - stop_pct)
    if gain >= BREAKEVEN_GAIN_PCT - _EPS:
        return avg_price
    return avg_price * (1 - stop_pct)


def position_context(decisions_path) -> dict:
    """Map ticker -> {"open_date", "entry_price"} for currently-open positions.

    The open date anchors the high-water window; the entry price is a fallback
    for when the portfolio payload doesn't carry ``avg_price``, because a
    position with no stop at all is the worst outcome here.

    Replaying the log in date order means a ticker that was sold and re-bought
    reports the later entry, so a previous position's peak can't inflate the
    high-water mark.
    """
    path = Path(decisions_path)
    if not path.exists():
        return {}

    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue  # a partially written line must not break the run
    records.sort(key=lambda r: r.get("date") or "")

    context: dict = {}
    for rec in records:
        action = (rec.get("action") or "").upper()
        if action == "CLOSE_ALL":
            context.clear()
            continue
        ticker = rec.get("ticker")
        if not ticker:
            continue
        if action == "BUY":
            context.setdefault(ticker, {"open_date": rec.get("date"),
                                        "entry_price": rec.get("entry_price"),
                                        "stop_loss_price": rec.get("stop_loss_price")})
        elif action in _EXIT_ACTIONS:
            context.pop(ticker, None)
    return context


def manage_stop_losses(client, holdings: dict, pending_orders: list,
                       decisions_path, *, price_fn, high_water_fn,
                       stop_pct=None) -> dict:
    """Place missing stop-losses and ratchet existing ones upward.

    Returns counts of {"placed", "raised"}. Data sources are injected so the
    ratchet logic can be tested without network access.
    """
    if stop_pct is None:
        stop_pct = config.STOP_LOSS_DEFAULT_PCT

    existing = {}
    for order in pending_orders or []:
        if (order.get("action") or "").upper() == "STOP_LOSS":
            price = order.get("limit_price")
            if price:
                existing[order.get("ticker")] = float(price)

    context = position_context(decisions_path)
    placed = raised = 0

    for ticker, holding in (holdings or {}).items():
        logged = context.get(ticker) or {}
        avg_price = (holding.get("avg_price") or holding.get("entry_price")
                     or logged.get("entry_price"))
        price = price_fn(ticker)
        if not price:
            continue

        if avg_price:
            # Only pay for history when the position is in the trail band.
            high_water = None
            if price / avg_price - 1 >= TRAIL_GAIN_PCT - _EPS:
                high_water = high_water_fn(ticker, logged.get("open_date"))
            target = target_stop_price(avg_price, price, high_water, stop_pct)
        else:
            # No entry reference anywhere, so the position can't be ratcheted.
            # Fall back to the stop the Judge chose when it opened the trade —
            # an un-ratcheted stop still beats an unprotected position.
            target = logged.get("stop_loss_price")

        if target is None:
            continue
        target = round(target, 2)

        if target >= price:
            continue  # a stop at or above market would fire immediately

        current = existing.get(ticker)
        if current is not None:
            # Below the breakeven band there is nothing to protect yet, so leave
            # the stop the Judge chose alone. Overriding it with a mechanical
            # entry-based stop would tighten stops on losing positions and exit
            # them near their lows — the opposite of the intent here.
            if avg_price and price / avg_price - 1 < BREAKEVEN_GAIN_PCT - _EPS:
                continue
            if target <= current * (1 + MIN_CHANGE_PCT):
                continue  # never loosen, and skip trivial raises

        result = client.submit_trade(
            "STOP_LOSS", ticker, limit_price=target,
            why=stop_loss_why(ticker, stop_price=target, entry_price=avg_price))
        if result.get("status") == "error":
            log.info("STOP_LOSS | failed for %s: %s", ticker, result.get("error_code"))
            continue

        if current is None:
            placed += 1
            log.info("STOP_LOSS | placed for %s at $%s", ticker, target)
        else:
            raised += 1
            log.info("STOP_LOSS | raised for %s $%s -> $%s (gain %+.1f%%)",
                     ticker, current, target, (price / avg_price - 1) * 100)

    return {"placed": placed, "raised": raised}
