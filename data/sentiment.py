import xml.etree.ElementTree as ET

import requests

from config import get_logger

log = get_logger()

_STOCKTWITS = "https://api.stocktwits.com/api/2/streams/symbol/{}.json"
_ST_HEADERS = {
    "User-Agent": "stockbeat-agent/0.1 (+https://github.com/stockbeat)",
    "Accept": "application/json",
}


def get_stocktwits(ticker: str, session=None, timeout: int = 10) -> dict:
    """StockTwits bull/bear counts. Returns {} on any error/timeout/non-200."""
    sess = session or requests
    try:
        resp = sess.get(_STOCKTWITS.format(ticker), timeout=timeout,
                        headers=_ST_HEADERS)
        if resp.status_code != 200:
            return {}
        messages = resp.json().get("messages", [])
    except Exception as exc:
        log.info("DATA | stocktwits failed for %s: %s", ticker, exc)
        return {}

    bullish = bearish = 0
    for m in messages:
        basic = ((m.get("entities") or {}).get("sentiment") or {})
        label = basic.get("basic") if isinstance(basic, dict) else None
        if label == "Bullish":
            bullish += 1
        elif label == "Bearish":
            bearish += 1
    return {"bullish": bullish, "bearish": bearish, "messages": len(messages)}


_REDDIT_RSS = ("https://www.reddit.com/r/wallstreetbets+stocks+investing/"
               "search.rss?q={}&restrict_sr=on&sort=new&t=week&limit=10")
_RD_HEADERS = {
    "User-Agent": "stockbeat-agent/0.1 (+https://github.com/stockbeat)",
}
_ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}


def get_reddit(ticker: str, session=None, timeout: int = 10) -> dict:
    """Reddit mention count via RSS feed. Returns {} on any error/timeout."""
    sess = session or requests
    try:
        resp = sess.get(_REDDIT_RSS.format(ticker), timeout=timeout,
                        headers=_RD_HEADERS)
        if resp.status_code != 200:
            return {}
        root = ET.fromstring(resp.text)
        entries = root.findall("atom:entry", _ATOM_NS)
    except Exception as exc:
        log.info("DATA | reddit failed for %s: %s", ticker, exc)
        return {}
    return {"mentions": len(entries)}
