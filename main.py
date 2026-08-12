import argparse
import random
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import config
from analysis.llm import LLMClient, build_client
from analysis.pipeline import run_comment_replies, run_debate
from data.enrichment import enrich_candidates
from data.fundamentals import get_sector
from data.macro import get_macro
from data.market import (batch_download, excursion_since, get_history,
                         high_water_since, window_return)
from data.screener import apply_sector_cap, build_indicators, score_ticker, select_candidates
from execution.stockbeat_client import StockbeatClient
from execution.stops import manage_stop_losses, position_context
from execution.validator import validate_buy, validate_sell
from tickers import normalize_ticker
from memory.memory import (extract_features, grade_open_decisions,
                           log_decisions, recent_lessons)
from memory.report import write_run_report
from profiles import load_profile

log = config.get_logger()
ET = ZoneInfo("America/New_York")


def _memory_path() -> Path:
    """Return the decisions log path for the current agent (dynamic per agent)."""
    return config.agent_memory_dir() / "decisions.jsonl"


def _apply_agent_profile(name: str) -> None:
    """Load a profile JSON and apply its settings to config."""
    profile = load_profile(name)
    config.AGENT_NAME = profile["name"]
    config.AGENT_PERSONA = profile["persona"]
    config.STOCKBEAT_API_KEY = profile["api_key"]
    overrides = profile.get("risk_overrides", {})
    if "cash_target_default" in overrides:
        config.CASH_TARGET_DEFAULT = overrides["cash_target_default"]
    if "position_pct_default" in overrides:
        config.POSITION_PCT_DEFAULT = overrides["position_pct_default"]
    if "stop_loss_default_pct" in overrides:
        config.STOP_LOSS_DEFAULT_PCT = overrides["stop_loss_default_pct"]
    if "min_holding_days" in overrides:
        config.MIN_HOLDING_DAYS = overrides["min_holding_days"]


def _screen(tickers: list) -> tuple:
    """Batch-download + score every ticker. Returns (scores, indicators_by_ticker)."""
    frames = batch_download(tickers)
    scores, indicators_by_ticker = {}, {}
    for ticker, df in frames.items():
        ind = build_indicators(df)
        if not ind:
            continue
        indicators_by_ticker[ticker] = ind
        scores[ticker] = score_ticker(ind)
    return scores, indicators_by_ticker


def _current_price(ticker):
    df = get_history(ticker, period="5d")
    close = df["Close"].dropna() if not df.empty and "Close" in df else None
    return float(close.iloc[-1]) if close is not None and len(close) else None


def _held_days(decisions_path, run_date: str) -> dict:
    """Map ticker -> calendar days the current position has been held."""
    try:
        today = datetime.strptime(run_date, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return {}
    out = {}
    for ticker, ctx in position_context(decisions_path).items():
        opened = ctx.get("open_date")
        if not opened:
            continue
        try:
            out[ticker] = (today - datetime.strptime(opened, "%Y-%m-%d").date()).days
        except ValueError:
            continue
    return out


def _manage_stops(client, holdings: dict, pending_orders: list, decisions_path) -> dict:
    """Place missing stop-losses and ratchet existing ones (see execution.stops)."""
    return manage_stop_losses(
        client, holdings, pending_orders, decisions_path,
        price_fn=_current_price, high_water_fn=high_water_since)


def _default_client():
    try:
        from execution.mcp_client import McpStockbeatClient
        client = McpStockbeatClient(config.STOCKBEAT_API_KEY,
                                     config.STOCKBEAT_BASE_URL,
                                     dry_run=config.DRY_RUN)
        client.connect()
        return client
    except Exception as exc:
        log.info("MCP | fallback to REST client: %s", exc)
        return StockbeatClient(config.STOCKBEAT_API_KEY,
                               config.STOCKBEAT_BASE_URL,
                               dry_run=config.DRY_RUN)


def _handle_comment_replies(client, llm, now_et) -> list:
    try:
        data = client.list_comments(limit=50)
    except Exception as exc:
        log.info("COMMENTS | fetch failed: %s", exc)
        return []

    raw_comments = data.get("comments", [])
    if not raw_comments:
        log.info("COMMENTS | no comments")
        return []

    cutoff = now_et - timedelta(hours=config.COMMENT_MAX_AGE_HOURS)
    eligible = []
    for c in raw_comments:
        if c.get("replied"):
            continue
        created = c.get("created_at", "")
        try:
            ts = datetime.fromisoformat(created.replace("Z", "+00:00"))
            if ts < cutoff.astimezone(ts.tzinfo if ts.tzinfo else None):
                continue
        except (ValueError, TypeError):
            continue
        hours_ago = (now_et.astimezone() - ts).total_seconds() / 3600
        if hours_ago < 1:
            age_label = "just now"
        else:
            age_label = f"{int(hours_ago)}h ago"
        eligible.append({
            "comment_id": c["id"], "body": c["body"],
            "trade_ticker": c.get("trade_ticker", ""),
            "trade_action": c.get("trade_action", ""),
            "trade_why": c.get("trade_why", ""),
            "comments_count": c.get("comments_count", 0),
            "age_label": age_label,
        })

    if not eligible:
        log.info("COMMENTS | no eligible comments (all replied or >%dh old)",
                 config.COMMENT_MAX_AGE_HOURS)
        return []

    log.info("COMMENTS | %d eligible comments to review", len(eligible))
    replies = run_comment_replies(llm, {"comments": eligible})

    submitted = []
    for r in replies:
        body = r.get("reply", "")[:config.COMMENT_REPLY_MAX_LEN]
        try:
            result = client.reply_to_comment(r["comment_id"], body)
            if result.get("status") != "error":
                submitted.append(r)
                log.info("REPLY | %s -> %s", r["comment_id"], body[:50])
            else:
                log.info("REPLY | failed %s: %s", r["comment_id"],
                         result.get("error_code"))
        except Exception as exc:
            log.info("REPLY | error %s: %s", r["comment_id"], exc)

    return submitted


def _default_llm() -> LLMClient:
    return build_client()


def run(now_et=None, client=None, llm=None) -> dict:
    now_et = now_et or datetime.now(ET)
    client = client or _default_client()
    llm = llm or _default_llm()

    portfolio = client.get_portfolio()
    tokens = portfolio.get("trade_tokens", 0)
    log.info("START | trade_tokens=%s", tokens)

    # Grading is local-only and stop updates consume no trade tokens, so both
    # run before the token check. An agent that has spent its tokens still needs
    # its winners protected — that is precisely when the ratchet matters most.
    run_date = now_et.strftime("%Y-%m-%d")
    graded = grade_open_decisions(_memory_path(), window_fn=window_return,
                                  excursion_fn=excursion_since, run_date=run_date)
    log.info("MEMORY | %s new checkpoints, %s decisions fully graded",
             graded["checkpoints"], graded["graded"])

    pending_orders = client.get_pending_orders()
    stops = _manage_stops(client, portfolio.get("holdings", {}),
                          pending_orders, _memory_path())
    if stops["placed"] or stops["raised"]:
        log.info("STOP_LOSS | %s placed, %s raised", stops["placed"], stops["raised"])

    if not tokens:
        log.info("END | no trade tokens")
        return {"status": "no_tokens", "trades": 0, "actions": 0,
                "candidates": 0, "stops": stops}

    universe = client.get_universe()
    holdings = list(portfolio.get("holdings", {}).keys())

    # Mode detection (needed before shortlist sizing).
    is_initial = not holdings and tokens >= config.INITIAL_BUILD_MIN_TOKENS
    mode = "initial" if is_initial else "normal"
    max_actions = config.INITIAL_BUILD_MAX_ACTIONS if is_initial else config.MAX_TRADES_NORMAL
    shortlist_bull = config.SHORTLIST_BULL + (5 if is_initial else 0)

    if universe:
        pool = [t for t in universe if t not in holdings]
        random.shuffle(pool)
        scan_list = list(holdings) + pool[:config.SCAN_SAMPLE_SIZE]
    else:
        scan_list = config.SKELETON_TICKERS
    scores, indicators_by_ticker = _screen(scan_list)
    candidate_tickers = select_candidates(
        scores, holdings, top_bull=config.PRELIM_BULL, top_bear=config.PRELIM_BEAR)
    log.info("SCREEN | %s/%s sampled, %s prelim candidates",
             len(scan_list), len(universe or scan_list), len(candidate_tickers))

    # Sector cap → second shortlist → macro → enrich.
    capped = apply_sector_cap(candidate_tickers, get_sector, holdings,
                              max_per_sector=config.MAX_PER_SECTOR)
    shortlist = select_candidates(
        {t: scores.get(t, 0) for t in capped}, holdings,
        top_bull=shortlist_bull, top_bear=config.SHORTLIST_BEAR)
    macro = get_macro()
    candidates = enrich_candidates(shortlist, indicators_by_ticker, scores)
    random.shuffle(candidates)
    log.info("DATA | enriched %s candidates; macro=%s", len(candidates), bool(macro))

    lessons = ""
    if candidates:
        lessons = recent_lessons(_memory_path(), candidates[0]["ticker"])
    context = {"mode": mode, "max_actions": max_actions,
               "portfolio": portfolio, "candidates": candidates,
               "macro": macro, "lessons": lessons}
    risk_assessment, actions, transcript = run_debate(llm, context)
    log.info("LLM:JUDGE | %s actions, stance=%s",
             len(actions), (risk_assessment or {}).get("stance", "default"))

    price_by_ticker = {c["ticker"]: c["price"] for c in candidates}

    buys, sells = [], []
    seen_buy_tickers = set()
    committed_cash = 0.0
    held_days_by_ticker = _held_days(_memory_path(), run_date)
    for action in actions:
        kind = (action.get("action") or "").upper()
        if kind == "BUY":
            ticker = normalize_ticker(action.get("ticker", ""))
            if ticker in seen_buy_tickers:
                continue
            price = price_by_ticker.get(action.get("ticker"))
            # Each validation must see the cash earlier ones already claimed.
            # Validating every buy against the same run-start snapshot is how a
            # 10-action initial build spent 100% of equity despite a 20% cash
            # target, leaving the agent unable to trade at all afterwards.
            remaining = dict(portfolio)
            remaining["available_cash"] = max(
                0.0, float(portfolio.get("available_cash") or 0) - committed_cash)
            valid = validate_buy(action, remaining, universe, price or 0,
                                 risk_assessment=risk_assessment)
            if valid:
                committed_cash += float(valid.get("usd_amount") or 0)
                seen_buy_tickers.add(valid["ticker"])
                valid["entry_price"] = price_by_ticker.get(valid["ticker"])
                raw_stop = action.get("stop_loss_price")
                if price:
                    min_stop = round(price * (1 - config.STOP_LOSS_MAX_PCT), 2)
                    max_stop = round(price * (1 - config.STOP_LOSS_MIN_PCT), 2)
                    default_stop = round(price * (1 - config.STOP_LOSS_DEFAULT_PCT), 2)
                    if isinstance(raw_stop, (int, float)) and min_stop <= raw_stop <= max_stop:
                        stop = raw_stop
                    else:
                        stop = default_stop
                    valid["stop_loss_price"] = stop
                else:
                    stop = None
                buys.append((valid, stop))
        elif kind == "SELL":
            ticker = normalize_ticker(action.get("ticker", ""))
            valid = validate_sell(action, portfolio, universe,
                                  held_days=held_days_by_ticker.get(ticker))
            if valid:
                valid["entry_price"] = price_by_ticker.get(valid["ticker"])
                sells.append(valid)

    executed = []
    for valid, stop in buys + [(s, None) for s in sells]:
        if len(executed) >= max_actions:
            break
        is_buy = valid["action"] == "BUY"
        result = client.submit_trade(
            valid["action"], valid["ticker"], usd_amount=valid.get("usd_amount"),
            why=valid.get("why"),
            target_price=valid.get("target_price") if is_buy else None,
            target_horizon_days=valid.get("target_horizon_days") if is_buy else None)
        if result.get("status") == "error":
            log.info("SKIP | %s %s rejected: %s",
                     valid["action"], valid["ticker"], result.get("error_code"))
            continue
        executed.append(valid)

    comment_replies = _handle_comment_replies(client, llm, now_et)
    if comment_replies:
        log.info("COMMENTS | %d replies submitted", len(comment_replies))

    # Capture the evidence each decision was made on, so later analysis can ask
    # which signals actually predicted alpha rather than only what was traded.
    candidate_by_ticker = {c["ticker"]: c for c in candidates}
    regime = (macro or {}).get("regime")
    features_by_ticker = {
        rec["ticker"]: extract_features(
            candidate_by_ticker.get(rec["ticker"]),
            extra={"mode": mode, "regime": regime,
                   "stance": (risk_assessment or {}).get("stance")})
        for rec in executed
    }
    log_decisions(executed, path=_memory_path(), run_date=run_date,
                  features_by_ticker=features_by_ticker)
    write_run_report(run_date, portfolio=portfolio, scan_total=len(scan_list),
                     scored=len(scores), candidates=candidates, macro=macro,
                     transcript=transcript, actions=actions, executed=executed,
                     mode=mode, risk_assessment=risk_assessment,
                     comment_replies=comment_replies)
    log.info("END | %s trades executed", len(executed))
    return {"status": "ok", "trades": len(executed),
            "actions": len(actions), "candidates": len(candidates)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent", help="Agent profile name (e.g. momentum-mike)")
    args = parser.parse_args()
    if args.agent:
        _apply_agent_profile(args.agent)
    run()
