from data.fundamentals import get_fundamentals
from data.news import get_news
from data.sentiment import get_reddit, get_stocktwits


def enrich_candidates(tickers: list, indicators_by_ticker: dict,
                      scores: dict) -> list:
    """Assemble one candidate dict per ticker from all data sources.

    Tickers with no indicators entry are silently skipped.
    Each data fetch is graceful — missing/erroring source returns empty value.
    """
    out = []
    for ticker in tickers:
        ind = indicators_by_ticker.get(ticker)
        if not ind:
            continue
        fund = get_fundamentals(ticker)
        news = get_news(ticker)
        stocktwits = get_stocktwits(ticker)
        reddit = get_reddit(ticker)
        out.append({
            "ticker": ticker,
            "price": ind.get("price"),
            "score": scores.get(ticker, 0),
            "rsi": ind.get("rsi"),
            "macd_hist": ind.get("macd_hist"),
            "above_sma200": ind.get("above_sma200"),
            "golden_cross": ind.get("golden_cross"),
            "death_cross": ind.get("death_cross"),
            "volume_spike": ind.get("volume_spike"),
            "pe": fund.get("pe"),
            "forward_pe": fund.get("forward_pe"),
            "revenue_growth": fund.get("revenue_growth"),
            "market_cap": fund.get("market_cap"),
            "profit_margin": fund.get("profit_margin"),
            "sector": fund.get("sector"),
            "news": news,
            "stocktwits": stocktwits,
            "reddit": reddit,
            "_data_health": {
                "indicators": True,
                "fundamentals": bool(fund),
                "news": len(news) > 0,
                "stocktwits": bool(stocktwits),
                "reddit": bool(reddit),
            },
        })
    return out
