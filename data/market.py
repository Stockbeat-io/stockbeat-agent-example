import pandas as pd
import yfinance as yf

from config import get_logger

log = get_logger()


def batch_download(tickers: list, period: str = "60d",
                   interval: str = "1d") -> dict:
    """Download all tickers in one call. Returns {ticker: DataFrame}; {} on error."""
    if not tickers:
        return {}
    try:
        data = yf.download(tickers, period=period, interval=interval,
                           group_by="ticker", progress=False, threads=True)
    except Exception as exc:
        log.info("DATA | batch_download failed: %s", exc)
        return {}

    out = {}
    if len(tickers) == 1:
        t = tickers[0]
        if not data.empty and "Close" in data:
            out[t] = data.dropna(how="all")
        return out

    for t in tickers:
        try:
            sub = data[t] if t in data.columns.get_level_values(0) else None
        except Exception:
            sub = None
        if sub is None:
            continue
        sub = sub.dropna(how="all")
        if not sub.empty and "Close" in sub:
            out[t] = sub
    return out


def get_history(ticker: str, period: str = "60d", interval: str = "1d") -> pd.DataFrame:
    """Fetch OHLCV history for one ticker. Returns empty DataFrame on error."""
    try:
        df = yf.Ticker(ticker).history(period=period, interval=interval)
        if df is None:
            return pd.DataFrame()
        return df
    except Exception as exc:  # graceful: never raise to orchestrator
        log.info("DATA | get_history failed for %s: %s", ticker, exc)
        return pd.DataFrame()


def get_return_since(ticker: str, since_date: str):
    try:
        df = yf.Ticker(ticker).history(start=since_date)
        close = df["Close"].dropna()
        if len(close) < 2:
            return None
        return float((close.iloc[-1] / close.iloc[0] - 1) * 100)
    except Exception as exc:
        log.info("DATA | get_return_since failed for %s: %s", ticker, exc)
        return None


def get_spy_return(since_date: str):
    return get_return_since("SPY", since_date)


# Grading replays the same (ticker, date) windows for many decisions in one run,
# so closes are cached for the life of the process.
_closes_cache: dict = {}


def _closes_since(ticker: str, since_date: str):
    """Daily closes from `since_date` onward, or None if unavailable."""
    if not ticker or not since_date:
        return None
    key = (ticker, since_date)
    if key not in _closes_cache:
        try:
            df = yf.Ticker(ticker).history(start=since_date)
            close = df["Close"].dropna() if not df.empty and "Close" in df else None
            if close is not None and len(close) == 0:
                close = None
        except Exception as exc:
            log.info("DATA | history failed for %s since %s: %s", ticker, since_date, exc)
            close = None
        _closes_cache[key] = close
    return _closes_cache[key]


def window_return(ticker: str, since_date: str, trading_days: int):
    """% change over exactly `trading_days` sessions from the first session on
    or after `since_date`.

    Returns None when the window has not fully elapsed. That distinction is the
    whole point: grading a partial window would score a 60-day thesis on
    whatever days happen to have passed, which is what the old 1-day
    resolution did.
    """
    close = _closes_since(ticker, since_date)
    if close is None or trading_days is None or trading_days < 1:
        return None
    if trading_days >= len(close):
        return None
    return float((close.iloc[trading_days] / close.iloc[0] - 1) * 100)


def high_water_since(ticker: str, since_date: str):
    """Highest close since `since_date`, used as the trailing-stop anchor."""
    close = _closes_since(ticker, since_date)
    if close is None:
        return None
    return float(close.max())


def excursion_since(ticker: str, since_date: str, entry_price):
    """(max favourable, max adverse) % excursion since entry, or None.

    How far a position ran in each direction is what shows whether a trailing
    stop would have captured the move — the return at a checkpoint alone can't.
    """
    close = _closes_since(ticker, since_date)
    if close is None or not entry_price:
        return None
    return (round(float((close.max() / entry_price - 1) * 100), 2),
            round(float((close.min() / entry_price - 1) * 100), 2))
