import asyncio
import json

from config import ACTIONS_REQUIRING_LLM_MODEL, LLM_MODEL, get_logger

log = get_logger()

_ACTION_TO_TOOL = {
    "BUY": "buy",
    "SELL": "sell",
    "CLOSE_STOCK": "close_position",
    "CLOSE_ALL": "close_all",
    "BUY_LIMIT": "place_limit_buy",
    "STOP_LOSS": "set_stop_loss",
    "CANCEL_ORDER": "cancel_order",
}

_WRITE_TOOLS = set(_ACTION_TO_TOOL.values()) | {"post_insight", "reply_to_comment"}


class McpStockbeatClient:
    def __init__(self, api_key: str, base_url: str, dry_run: bool = True,
                 llm_model: str = ""):
        self.api_key = api_key
        self.mcp_url = base_url.rstrip("/") + "/mcp"
        self.dry_run = dry_run
        self.llm_model = llm_model or LLM_MODEL
        self.tools: list[str] = []
        self.server_instructions: str = ""
        self._session = None
        self._loop = None
        self._cm_transport = None
        self._cm_session = None

    def connect(self):
        try:
            self._loop = asyncio.new_event_loop()
            self._loop.run_until_complete(self._connect_async())
        except Exception as exc:
            log.info("MCP | connection failed: %s", exc)
            raise

    async def _connect_async(self):
        import httpx2
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client

        self._http_client = httpx2.AsyncClient(
            headers={"Authorization": f"Bearer {self.api_key}"},
        )
        self._cm_transport = streamable_http_client(
            self.mcp_url,
            http_client=self._http_client,
        )
        r, w = await self._cm_transport.__aenter__()

        self._cm_session = ClientSession(r, w)
        self._session = await self._cm_session.__aenter__()
        await self._session.initialize()

        tools_result = await self._session.list_tools()
        self.tools = [t.name for t in tools_result.tools]
        log.info("MCP | connected, %d tools: %s", len(self.tools), self.tools)

    def _call_tool(self, name: str, args: dict | None = None):
        if not self._session:
            raise RuntimeError("MCP client not connected — call connect() first")
        result = self._loop.run_until_complete(
            self._session.call_tool(name, args or {})
        )
        # The SDK exposes this as `is_error`; `isError` is only the wire alias,
        # so checking the wire name silently missed every rejection. Both are
        # read so an SDK that renames it back cannot re-open the same hole.
        is_error = getattr(result, "is_error", None)
        if is_error is None:
            is_error = getattr(result, "isError", False)

        if is_error:
            text = result.content[0].text if result.content else "unknown error"
            log.info("MCP | tool %s error: %s", name, text)
            parts = text.split(": ", 1)
            return {"status": "error",
                    "error_code": parts[0] if len(parts) == 2 else "ERR_UNKNOWN",
                    "message": parts[1] if len(parts) == 2 else text}

        text = result.content[0].text if result.content else "{}"
        try:
            return json.loads(text)
        except ValueError:
            # Schema rejections arrive as prose. Returning an error dict keeps
            # a bad response to one skipped trade instead of a dead run.
            log.info("MCP | tool %s returned non-JSON: %s", name, text[:200])
            return {"status": "error", "error_code": "ERR_BAD_RESPONSE",
                    "message": text[:200]}

    def get_portfolio(self) -> dict:
        return self._call_tool("get_portfolio")

    def get_universe(self) -> set:
        data = self._call_tool("get_universe")
        return set(data.get("tickers", []))

    def get_pending_orders(self) -> list:
        data = self._call_tool("list_pending_orders")
        if isinstance(data, dict):
            return data.get("orders", [])
        return data or []

    def get_quota(self) -> dict:
        return self._call_tool("get_quota")

    def get_trade_history(self, page: int = 1) -> dict:
        return self._call_tool("list_trades", {"page": page})

    def list_comments(self, trade_id: str | None = None,
                      limit: int = 20) -> dict:
        args = {"limit": limit}
        if trade_id is not None:
            args["trade_id"] = trade_id
        return self._call_tool("list_comments", args)

    def reply_to_comment(self, comment_id: str, body: str) -> dict:
        if self.dry_run:
            log.info("REPLY(DRY_RUN) | comment=%s body=%s", comment_id, body[:50])
            return {"status": "dry_run", "comment_id": comment_id}
        return self._call_tool("reply_to_comment",
                               {"comment_id": comment_id, "body": body})

    def submit_trade(self, action: str, ticker: str, usd_amount=None,
                     why=None, target_price=None, target_horizon_days=None,
                     limit_price=None) -> dict:
        tool_name = _ACTION_TO_TOOL.get(action.upper())
        if not tool_name:
            log.info("MCP | unknown action: %s", action)
            return {"status": "error", "error_code": "ERR_UNKNOWN_ACTION",
                    "ticker": ticker}

        if self.dry_run:
            log.info("TRADE(DRY_RUN) | %s %s %s", action, ticker, usd_amount)
            return {"status": "dry_run", "action": action, "ticker": ticker}

        args = {"ticker": ticker}
        if action.upper() in ACTIONS_REQUIRING_LLM_MODEL:
            args["llm_model"] = self.llm_model
        if usd_amount is not None:
            args["usd_amount"] = usd_amount
        if why is not None:
            args["why"] = why
        if target_price is not None:
            args["target_price"] = target_price
        if target_horizon_days is not None:
            args["target_horizon_days"] = target_horizon_days
        if limit_price is not None:
            args["limit_price"] = limit_price

        return self._call_tool(tool_name, args)

    def close(self):
        if self._loop and not self._loop.is_closed():
            self._loop.run_until_complete(self._close_async())
            self._loop.close()

    async def _close_async(self):
        if self._cm_session:
            try:
                await self._cm_session.__aexit__(None, None, None)
            except Exception:
                pass
        if self._cm_transport:
            try:
                await self._cm_transport.__aexit__(None, None, None)
            except Exception:
                pass
