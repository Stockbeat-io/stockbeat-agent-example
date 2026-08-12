import requests

from config import get_logger

log = get_logger()


class StockbeatClient:
    def __init__(self, api_key: str, base_url: str, dry_run: bool = True, session=None):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.dry_run = dry_run
        self.session = session or requests.Session()

    def _headers(self) -> dict:
        return {"X-API-Key": self.api_key, "Content-Type": "application/json"}

    def _get(self, path: str) -> dict:
        url = f"{self.base_url}/api/v1{path}"
        resp = self.session.get(url, headers=self._headers(), timeout=30)
        return resp.json()

    def get_portfolio(self) -> dict:
        return self._get("/portfolio")

    def get_universe(self) -> set:
        data = self._get("/universe")
        return set(data.get("tickers", []))

    def get_pending_orders(self) -> list:
        data = self._get("/orders/pending")
        if isinstance(data, dict):
            return data.get("orders", [])
        return data or []

    def submit_trade(self, action: str, ticker: str, usd_amount=None, why=None,
                     target_price=None, target_horizon_days=None,
                     limit_price=None) -> dict:
        body = {"action": action, "ticker": ticker}
        if usd_amount is not None:
            body["usd_amount"] = usd_amount
        if why is not None:
            body["why"] = why
        if target_price is not None:
            body["target_price"] = target_price
        if target_horizon_days is not None:
            body["target_horizon_days"] = target_horizon_days
        if limit_price is not None:
            body["limit_price"] = limit_price

        if self.dry_run:
            log.info("TRADE(DRY_RUN) | %s %s %s", action, ticker, usd_amount)
            return {"status": "dry_run", "action": action, "ticker": ticker}

        url = f"{self.base_url}/api/v1/trades"
        try:
            resp = self.session.post(url, headers=self._headers(), json=body,
                                     timeout=30)
            data = resp.json()
        except Exception as exc:  # skip-and-continue, never raise
            log.info("TRADE | POST failed for %s %s: %s", action, ticker, exc)
            return {"status": "error", "error_code": "ERR_REQUEST", "ticker": ticker}

        if resp.status_code >= 400:
            log.info("TRADE | %s %s rejected: %s", action, ticker,
                     data.get("error_code"))
            return {"status": "error", "error_code": data.get("error_code"),
                    "ticker": ticker}
        return data
