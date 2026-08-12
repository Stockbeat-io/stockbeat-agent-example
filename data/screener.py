import math
import random

import pandas as pd

from data.indicators import macd, rsi, sma


def _is_nan(x) -> bool:
    return isinstance(x, float) and math.isnan(x)


def build_indicators(df: pd.DataFrame) -> dict:
    if df is None or df.empty or "Close" not in df:
        return {}
    close = df["Close"].dropna()
    if close.empty:
        return {}

    sma50_now = sma(close, 50)
    sma200_now = sma(close, 200)
    # Cross detection: compare last two bars of the 50/200 relationship.
    prev_close = close.iloc[:-1]
    sma50_prev = sma(prev_close, 50)
    sma200_prev = sma(prev_close, 200)

    golden = death = False
    if not any(_is_nan(v) for v in (sma50_now, sma200_now, sma50_prev, sma200_prev)):
        golden = sma50_prev <= sma200_prev and sma50_now > sma200_now
        death = sma50_prev >= sma200_prev and sma50_now < sma200_now

    volume_spike = False
    if "Volume" in df:
        vol = df["Volume"].dropna()
        avg20 = sma(vol, 20)
        if not _is_nan(avg20) and avg20 > 0:
            volume_spike = float(vol.iloc[-1]) > 2 * avg20

    above_sma200 = (not _is_nan(sma200_now)) and float(close.iloc[-1]) > sma200_now

    return {
        "price": float(close.iloc[-1]),
        "rsi": rsi(close, 14),
        "macd_hist": macd(close)["hist"],
        "above_sma200": bool(above_sma200),
        "golden_cross": bool(golden),
        "death_cross": bool(death),
        "volume_spike": bool(volume_spike),
    }


def score_ticker(ind: dict) -> int:
    score = 0
    if ind.get("golden_cross"):
        score += 3
    if ind.get("death_cross"):
        score -= 3
    r = ind.get("rsi")
    if r is not None and not _is_nan(r):
        if r < 30:
            score += 2
        elif r > 70:
            score -= 2
    h = ind.get("macd_hist")
    if h is not None and not _is_nan(h):
        if h > 0:
            score += 2
        elif h < 0:
            score -= 2
    if ind.get("volume_spike"):
        score += 1
    if ind.get("above_sma200"):
        score += 1
    return score


def select_candidates(scores: dict, holdings: list, top_bull: int = 10,
                      top_bear: int = 5) -> list:
    items = list(scores.items())
    random.shuffle(items)
    ranked = sorted(items, key=lambda kv: kv[1], reverse=True)
    bull = [t for t, _ in ranked[:top_bull]]
    bear = [t for t, _ in ranked[-top_bear:]] if top_bear else []

    out: list = []
    for t in list(holdings) + bull + bear:
        if t not in out:
            out.append(t)
    return out


def apply_sector_cap(tickers: list, sector_fn, holdings: list,
                     max_per_sector: int = 3) -> list:
    """Return *tickers* with at most *max_per_sector* non-held tickers per sector.

    Rules (in priority order):
    - Holdings are always kept regardless of sector count.
    - Tickers whose sector_fn returns None are always kept.
    - Non-held tickers with a known sector are kept only until the per-sector
      count reaches max_per_sector; subsequent ones are dropped.
    - Input order is preserved.
    """
    holdings_set = set(holdings)
    sector_counts: dict = {}
    result: list = []

    for ticker in tickers:
        if ticker in holdings_set:
            result.append(ticker)
            continue
        sector = sector_fn(ticker)
        if sector is None:
            result.append(ticker)
            continue
        count = sector_counts.get(sector, 0)
        if count < max_per_sector:
            result.append(ticker)
            sector_counts[sector] = count + 1

    return result
