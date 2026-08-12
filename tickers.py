def normalize_ticker(ticker: str) -> str:
    """Normalize agent input to StockBeat canonical dash form (BRK.B -> BRK-B)."""
    return ticker.strip().upper().replace(".", "-").replace("/", "-")
