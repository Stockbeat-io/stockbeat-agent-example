import pandas as pd


def sma(series: pd.Series, period: int) -> float:
    if len(series) < period:
        return float("nan")
    return float(series.tail(period).mean())


def rsi(series: pd.Series, period: int = 14) -> float:
    if len(series) < period + 1:
        return float("nan")
    delta = series.diff().dropna()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = float(gain.tail(period).mean())
    avg_loss = float(loss.tail(period).mean())
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return float(100 - (100 / (1 + rs)))


def macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> dict:
    if len(series) < slow + signal:
        return {"macd": float("nan"), "signal": float("nan"), "hist": float("nan")}
    ema_fast = series.ewm(span=fast, adjust=False).mean()
    ema_slow = series.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    macd_val = float(macd_line.iloc[-1])
    signal_val = float(signal_line.iloc[-1])
    return {"macd": macd_val, "signal": signal_val, "hist": macd_val - signal_val}
