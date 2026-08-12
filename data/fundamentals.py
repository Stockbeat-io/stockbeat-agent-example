from functools import lru_cache

import yfinance as yf

from config import get_logger

log = get_logger()


def get_fundamentals(ticker: str) -> dict:
    """Fetch lean fundamentals. Returns {} on error; values may be None."""
    try:
        info = yf.Ticker(ticker).info or {}
        return {
            "pe": info.get("trailingPE"),
            "forward_pe": info.get("forwardPE"),
            "revenue_growth": info.get("revenueGrowth"),
            "market_cap": info.get("marketCap"),
            "profit_margin": info.get("profitMargins"),
            "sector": info.get("sector"),
        }
    except Exception as exc:
        log.info("DATA | get_fundamentals failed for %s: %s", ticker, exc)
        return {}


@lru_cache(maxsize=1024)
def get_sector(ticker: str):
    """Sector string for a ticker, cached. None on error."""
    try:
        return (yf.Ticker(ticker).info or {}).get("sector")
    except Exception as exc:
        log.info("DATA | get_sector failed for %s: %s", ticker, exc)
        return None
