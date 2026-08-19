import json
import subprocess
from typing import Protocol

import requests

import config
from config import get_logger

log = get_logger()


class LLMClient(Protocol):
    """The only interface the pipeline needs from an LLM backend."""

    def generate(self, prompt: str, system: str | None = None) -> str: ...


class _HTTPClient:
    """Shared transport. Subclasses supply URL, headers, body shape and parsing.

    `generate` never raises. Any transport, HTTP or parsing failure returns ""
    and is logged, so the pipeline falls through to no-trade rather than
    aborting a scheduled run midway through a portfolio.
    """

    def __init__(self, base_url: str, model: str, api_key: str = "",
                 session=None, timeout: int = 180):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.session = session or requests.Session()
        self.timeout = timeout

    def _url(self) -> str:
        raise NotImplementedError

    def _headers(self) -> dict:
        return {}

    def _body(self, prompt: str, system: str | None) -> dict:
        raise NotImplementedError

    def _parse(self, data: dict) -> str:
        raise NotImplementedError

    @staticmethod
    def _chat_messages(prompt: str, system: str | None) -> list[dict]:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        return messages

    def generate(self, prompt: str, system: str | None = None) -> str:
        try:
            resp = self.session.post(
                self._url(),
                json=self._body(prompt, system),
                headers=self._headers(),
                timeout=self.timeout,
            )
            return self._parse(resp.json())
        except Exception as exc:
            log.info("LLM | generate failed: %s", exc)
            return ""


class OllamaClient(_HTTPClient):
    def _url(self) -> str:
        return f"{self.base_url}/api/chat"

    def _body(self, prompt, system):
        return {
            "model": self.model,
            "messages": self._chat_messages(prompt, system),
            "stream": False,
        }

    def _parse(self, data):
        return data["message"]["content"]


class OpenAICompatClient(_HTTPClient):
    """OpenAI, LM Studio, vLLM, Groq, OpenRouter, Together.

    These differ only in `base_url` and model name, so one client covers them
    all. Point LLM_BASE_URL at the host and set LLM_MODEL accordingly.
    """

    def _url(self) -> str:
        return f"{self.base_url}/v1/chat/completions"

    def _headers(self):
        return {"Authorization": f"Bearer {self.api_key}"}

    def _body(self, prompt, system):
        return {
            "model": self.model,
            "messages": self._chat_messages(prompt, system),
            "stream": False,
        }

    def _parse(self, data):
        return data["choices"][0]["message"]["content"]


class AnthropicClient(_HTTPClient):
    API_VERSION = "2023-06-01"
    MAX_TOKENS = 4096

    def _url(self) -> str:
        return f"{self.base_url}/v1/messages"

    def _headers(self):
        return {
            "x-api-key": self.api_key,
            "anthropic-version": self.API_VERSION,
            "content-type": "application/json",
        }

    def _body(self, prompt, system):
        body = {
            "model": self.model,
            "max_tokens": self.MAX_TOKENS,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            # The Messages API takes `system` as a top-level field, not as a
            # message with role="system".
            body["system"] = system
        return body

    def _parse(self, data):
        return data["content"][0]["text"]


class ClaudeCLIClient:
    """Claude Code CLI in headless mode — for a Pro/Max subscription.

    A subscription has no API key: the credential is an OAuth token the CLI
    owns and refreshes itself, so there is no header to build and nothing for
    `_HTTPClient` to send. That is why this is a sibling of the HTTP clients
    rather than a subclass — it shells out instead of making a request.

    `base_url`, `api_key` and `session` are accepted and ignored so the class
    stays drop-in for `build_client`, which constructs every provider the same
    way.
    """

    def __init__(self, base_url: str, model: str, api_key: str = "",
                 session=None, timeout: int = 300, runner=subprocess.run):
        self.model = model
        self.timeout = timeout
        self._run = runner

    def _command(self, system: str | None) -> list[str]:
        # --setting-sources and --strict-mcp-config keep a run launched from the
        # repo out of this project's CLAUDE.md and MCP servers: cost and latency
        # for four text-generation calls that need neither. --tools "" drops the
        # built-in tool definitions for the same reason — measured against the
        # real CLI it took the per-call prompt from ~17k tokens to ~6.4k.
        #
        # Never add --bare here. It looks right for a scripted run, but it
        # restricts auth to ANTHROPIC_API_KEY and never reads the OAuth login,
        # which is the only credential a subscription has.
        cmd = ["claude", "-p", "--output-format", "json",
               "--strict-mcp-config", "--setting-sources", "", "--tools", ""]
        if self.model:
            cmd += ["--model", self.model]
        if system:
            cmd += ["--append-system-prompt", system]
        return cmd

    def generate(self, prompt: str, system: str | None = None) -> str:
        """Same contract as _HTTPClient.generate: never raises, "" on failure."""
        try:
            # The prompt goes on stdin, not argv — an analyst report plus a
            # bull/bear exchange runs well past a comfortable argument length.
            proc = self._run(
                self._command(system),
                input=prompt,
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
            if proc.returncode != 0:
                log.info("LLM | claude CLI exit %s: %s",
                         proc.returncode, (proc.stderr or "")[:200])
                return ""
            payload = json.loads(proc.stdout)
            # An API error can still parse as JSON, and `subtype` reads
            # "success" even then — `is_error` is the only honest signal. Its
            # `result` is an English apology, so returning it unchecked would
            # hand the pipeline an apology as if it were an analyst report.
            if payload.get("is_error"):
                log.info("LLM | claude CLI reported an error (status %s): %s",
                         payload.get("api_error_status"),
                         str(payload.get("result", ""))[:200])
                return ""
            return payload["result"]
        except Exception as exc:
            log.info("LLM | generate failed: %s", exc)
            return ""


_PROVIDERS = {
    "ollama": OllamaClient,
    "openai-compatible": OpenAICompatClient,
    "anthropic": AnthropicClient,
    "claude-cli": ClaudeCLIClient,
}

# Providers that carry their own credential and need no LLM_API_KEY: Ollama is
# local, and the Claude CLI holds a subscription OAuth token.
_NO_API_KEY_NEEDED = (OllamaClient, ClaudeCLIClient)


def build_client(session=None) -> LLMClient:
    """Construct the configured client, raising on misconfiguration.

    This raises rather than returning a dud client on purpose. A run that cannot
    reach an LLM must stop before fetching market data — otherwise the pipeline
    reads an empty analyst report as a genuine "no signal" and the operator
    never learns the provider was misconfigured.
    """
    provider = config.LLM_PROVIDER
    cls = _PROVIDERS.get(provider)
    if cls is None:
        raise ValueError(
            f"Unknown LLM_PROVIDER {provider!r}. "
            f"Expected one of: {', '.join(sorted(_PROVIDERS))}"
        )
    if not config.LLM_MODEL:
        raise ValueError(
            f"LLM_MODEL is required for LLM_PROVIDER={provider!r}. "
            "Set it in .env (for example LLM_MODEL=gpt-4o-mini)."
        )
    if cls not in _NO_API_KEY_NEEDED and not config.LLM_API_KEY:
        raise ValueError(
            f"LLM_API_KEY is required for LLM_PROVIDER={provider!r}. Set it in .env."
        )
    return cls(
        config.LLM_BASE_URL,
        config.LLM_MODEL,
        api_key=config.LLM_API_KEY,
        session=session,
    )
