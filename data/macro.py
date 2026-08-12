from config import FRED_API_KEY, FRED_SERIES, get_logger

log = get_logger()


def get_macro(api_key: str | None = None, client=None) -> dict:
    """Fetch key macro indicators from FRED.

    Parameters
    ----------
    api_key:
        FRED API key. Defaults to ``config.FRED_API_KEY``.
    client:
        Injectable FRED client for tests.  Must expose
        ``get_series(series_id) -> pd.Series``.

    Returns
    -------
    dict with keys ``fed_funds``, ``cpi_yoy``, ``ten_year``, ``gdp_growth``
    (all floats), or ``{}`` on missing key / any error.
    """
    key = api_key if api_key is not None else FRED_API_KEY

    if not key and client is None:
        return {}

    try:
        if client is None:
            from fredapi import Fred  # lazy import — only when actually needed
            client = Fred(api_key=key)

        fed_funds = float(client.get_series(FRED_SERIES["fed_funds"]).iloc[-1])
        ten_year = float(client.get_series(FRED_SERIES["ten_year"]).iloc[-1])
        gdp_growth = float(client.get_series(FRED_SERIES["gdp_growth"]).iloc[-1])

        cpi_series = client.get_series(FRED_SERIES["cpi"]).dropna()
        cpi_yoy = None
        if len(cpi_series) >= 13:
            cpi_yoy = round(float((cpi_series.iloc[-1] / cpi_series.iloc[-13] - 1) * 100), 2)

        return {
            "fed_funds": fed_funds,
            "cpi_yoy": cpi_yoy,
            "ten_year": ten_year,
            "gdp_growth": gdp_growth,
        }
    except Exception as exc:
        log.info("DATA | get_macro failed: %s", exc)
        return {}
