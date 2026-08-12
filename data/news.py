import yfinance as yf

from config import get_logger

log = get_logger()


def _extract_title(item: dict) -> str | None:
    """Handle both nested (content.title) and flat (title) yfinance formats."""
    content = item.get("content")
    if isinstance(content, dict):
        return content.get("title")
    return item.get("title")


def get_news(ticker: str, limit: int = 5) -> list:
    """Up to `limit` recent headline titles. Returns [] on error."""
    try:
        items = yf.Ticker(ticker).news or []
        titles = [_extract_title(i) for i in items]
        return [t for t in titles if t][:limit]
    except Exception as exc:
        log.info("DATA | get_news failed for %s: %s", ticker, exc)
        return []
