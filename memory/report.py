import json
from pathlib import Path

import config
from config import get_logger

# Keep LOG_DIR as a module-level alias for backwards compatibility with tests
# that monkeypatch report_mod.LOG_DIR. Functions read config.LOG_DIR dynamically.
LOG_DIR = config.LOG_DIR

log = get_logger()


def _report_log_dir() -> Path:
    """Return the log directory to write reports to, respecting AGENT_NAME."""
    log_dir = config.LOG_DIR
    if config.AGENT_NAME != "default":
        log_dir = config.LOG_DIR / config.AGENT_NAME
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir


def write_run_report(run_date: str, *, portfolio: dict, scan_total: int,
                     scored: int, candidates: list, macro: dict,
                     transcript: dict, actions: list, executed: list,
                     mode: str, risk_assessment: dict | None = None,
                     comment_replies: list | None = None) -> Path:
    """Write a JSON run report to logs/YYYY-MM-DD-report.json."""
    data_health = []
    for c in candidates:
        health = c.get("_data_health", {})
        data_health.append({
            "ticker": c["ticker"],
            "indicators": health.get("indicators", False),
            "fundamentals": health.get("fundamentals", False),
            "news": health.get("news", False),
            "stocktwits": health.get("stocktwits", False),
            "reddit": health.get("reddit", False),
        })

    sources_ok = sum(1 for h in data_health if all(h[k] for k in
                     ["indicators", "fundamentals", "news", "stocktwits", "reddit"]))

    report = {
        "run_date": run_date,
        "mode": mode,
        "portfolio_snapshot": {
            "total_equity": portfolio.get("total_equity"),
            "available_cash": portfolio.get("available_cash"),
            "trade_tokens": portfolio.get("trade_tokens"),
            "holdings": list(portfolio.get("holdings", {}).keys()),
        },
        "screening": {
            "universe_size": scan_total,
            "scored": scored,
            "shortlisted": len(candidates),
        },
        "data_health": {
            "all_sources_ok": sources_ok,
            "partial_data": len(data_health) - sources_ok,
            "macro_available": bool(macro),
            "per_ticker": data_health,
        },
        "debate": {
            "analyst": transcript.get("analyst", ""),
            "bull": transcript.get("bull", ""),
            "bear": transcript.get("bear", ""),
        },
        "judge": {
            "raw_actions": actions,
            "action_count": len(actions),
        },
        "risk_assessment": risk_assessment,
        "execution": {
            "trades_executed": len(executed),
            "trades": executed,
        },
        "comment_replies": {
            "count": len(comment_replies or []),
            "replies": comment_replies or [],
        },
    }

    path = _report_log_dir() / f"{run_date}-report.json"
    path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    log.info("REPORT | written to %s", path)
    return path
