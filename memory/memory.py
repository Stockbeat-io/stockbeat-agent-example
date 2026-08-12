"""Decision log and outcome grading.

A decision is graded against the thesis it actually stated. It stays ``open``
and accumulates alpha checkpoints as each window completes, becoming ``graded``
only once its own ``target_horizon_days`` window has elapsed.

This replaces an earlier scheme that resolved every decision on the next run —
one day later — and marked it final. That scored a 60-day thesis on one day of
noise, so every lesson the agents ever read was noise.
"""

import json
from pathlib import Path

# Fixed windows, in trading sessions, measured for every decision so agents stay
# comparable regardless of the horizons they each chose.
CHECKPOINT_DAYS = (("5d", 5), ("20d", 20), ("60d", 60))

# Statuses that still accept new checkpoints. "pending" is the legacy name and
# is treated as "open" so existing logs keep grading without a migration step.
OPEN_STATUSES = {"open", "pending"}

_EXIT_ACTIONS = {"SELL", "CLOSE_STOCK", "CLOSE_ALL"}

# Fields copied from the enriched candidate onto each decision, so later
# analysis can ask which evidence actually predicted alpha. Without these the
# log records what was done but not why, and no attribution is possible.
FEATURE_FIELDS = (
    "score", "rsi", "macd_hist", "above_sma200", "golden_cross", "death_cross",
    "volume_spike", "pe", "forward_pe", "revenue_growth", "market_cap",
    "profit_margin", "sector",
)


def _read(path: Path) -> list:
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue  # never let one bad line lose the rest of the log
    return records


def _write(path: Path, records: list) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec) + "\n")


def _trading_days(calendar_days: int) -> int:
    """Convert a stated horizon in calendar days to trading sessions.

    ``target_horizon_days`` is calendar time, but return windows are counted in
    sessions; treating 60 calendar days as 60 sessions would stretch the window
    by roughly 40%.
    """
    return max(1, round(calendar_days * 5 / 7))


def extract_features(candidate: dict, extra: dict | None = None) -> dict:
    """Pull the evidence behind a decision off its enriched candidate."""
    if not candidate:
        return dict(extra or {})
    features = {k: candidate.get(k) for k in FEATURE_FIELDS
                if candidate.get(k) is not None}
    stocktwits = candidate.get("stocktwits") or {}
    if isinstance(stocktwits, dict) and stocktwits.get("sentiment") is not None:
        features["stocktwits_sentiment"] = stocktwits.get("sentiment")
    reddit = candidate.get("reddit")
    if isinstance(reddit, (list, tuple)):
        features["reddit_mentions"] = len(reddit)
    news = candidate.get("news")
    if isinstance(news, (list, tuple)):
        features["news_count"] = len(news)
    features.update(extra or {})
    return features


def log_decisions(decisions: list, path, run_date: str,
                  features_by_ticker: dict | None = None) -> int:
    """Append decisions to the log as `open`, with their supporting evidence."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    features_by_ticker = features_by_ticker or {}
    count = 0
    with path.open("a", encoding="utf-8") as fh:
        for d in decisions:
            record = dict(d)
            record["date"] = run_date
            record["status"] = "open"
            record["features"] = features_by_ticker.get(record.get("ticker"))
            fh.write(json.dumps(record) + "\n")
            count += 1
    return count


def grade_open_decisions(path, *, window_fn, excursion_fn, run_date) -> dict:
    """Attach alpha checkpoints to open decisions as their windows complete.

    Checkpoints are write-once — a window is measured the first time it has
    fully elapsed and is never revised, so a decision's record of what it looked
    like at 5 days survives whatever happens by day 60.

    `window_fn(ticker, date, trading_days)` returns a % return, or None when the
    window is incomplete. `excursion_fn(ticker, date, entry_price)` returns
    (max_favorable_pct, max_adverse_pct) or None.
    """
    path = Path(path)
    if not path.exists():
        return {"graded": 0, "checkpoints": 0}

    records = _read(path)
    new_checkpoints = 0
    newly_graded = 0
    changed = False

    for rec in records:
        if rec.get("status") not in OPEN_STATUSES:
            continue
        ticker, date = rec.get("ticker"), rec.get("date")
        if not ticker or not date or date == run_date:
            continue

        sign = -1 if (rec.get("action") or "").upper() in _EXIT_ACTIONS else 1
        checkpoints = rec.setdefault("checkpoints", {})

        windows = list(CHECKPOINT_DAYS)
        horizon = rec.get("target_horizon_days")
        if isinstance(horizon, int) and horizon > 0:
            windows.append(("horizon", _trading_days(horizon)))

        for name, days in windows:
            if name in checkpoints:
                continue
            ret = window_fn(ticker, date, days)
            spy = window_fn("SPY", date, days)
            if ret is None or spy is None:
                continue
            checkpoints[name] = {
                "return_pct": round(ret, 2),
                "spy_return_pct": round(spy, 2),
                "alpha_pct": round(sign * (ret - spy), 2),
            }
            new_checkpoints += 1
            changed = True

        excursion = excursion_fn(ticker, date, rec.get("entry_price"))
        if excursion:
            favorable, adverse = excursion
            updated = {"max_favorable_pct": favorable, "max_adverse_pct": adverse}
            if rec.get("excursion") != updated:
                rec["excursion"] = updated
                changed = True

        if "horizon" in checkpoints:
            rec["status"] = "graded"
            newly_graded += 1
            changed = True
        elif rec.get("status") != "open":
            rec["status"] = "open"  # migrate legacy "pending"
            changed = True

    if changed:
        _write(path, records)
    return {"graded": newly_graded, "checkpoints": new_checkpoints}


def best_checkpoint(rec: dict) -> tuple:
    """Longest-elapsed checkpoint for a record, as (name, data), or (None, None).

    Prefers the decision's own horizon, then the longest completed fixed window.
    """
    checkpoints = rec.get("checkpoints") or {}
    for name in ("horizon", "60d", "20d", "5d"):
        if name in checkpoints:
            return name, checkpoints[name]
    outcome = rec.get("outcome")  # legacy single-outcome records
    if outcome and outcome.get("alpha_pct") is not None:
        return "legacy", outcome
    return None, None


def recent_lessons(path, ticker: str, n: int = 5) -> str:
    path = Path(path)
    if not path.exists():
        return ""
    records = _read(path)
    scored = [r for r in records if best_checkpoint(r)[1]]
    if not scored:
        return ""

    same = [r for r in scored if r.get("ticker") == ticker][-n:]
    others = [r for r in scored if r.get("ticker") != ticker][-3:]

    def _line(r):
        name, data = best_checkpoint(r)
        return (f"- {r['ticker']} {r.get('action')} on {r.get('date')} "
                f"[{name}]: return {data.get('return_pct')}%, "
                f"alpha {data.get('alpha_pct')}%")

    return "\n".join(_line(r) for r in same + others)
