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


def _with_system(prompt: str, system: str | None) -> str:
    """Fold the system prompt into the text, for CLIs with no flag for it."""
    return f"{system}\n\n{prompt}" if system else prompt


def _json_result(stdout: str, binary: str) -> str:
    """Parse the `{is_error, result}` payload Claude Code and Cursor both emit.

    An API error can still parse as JSON, and `subtype` reads "success" even
    then — `is_error` is the only honest signal. Its `result` is an English
    apology, so returning it unchecked would hand the pipeline an apology as if
    it were an analyst report.
    """
    payload = json.loads(stdout)
    if payload.get("is_error"):
        log.info("LLM | %s reported an error (status %s): %s",
                 binary,
                 payload.get("api_error_status"),
                 str(payload.get("result", ""))[:200])
        return ""
    return payload["result"]


class _CLIClient:
    """Shared subprocess transport for CLIs that carry their own credential.

    A subscription has no API key: the credential is an OAuth token the CLI owns
    and refreshes itself, so there is no header to build and nothing for
    `_HTTPClient` to send. That is why these are siblings of the HTTP clients
    rather than subclasses — they shell out instead of making a request.

    Subclasses supply argv, prompt routing and parsing. `base_url`, `api_key`
    and `session` are accepted and ignored so they stay drop-in for
    `build_client`, which constructs every provider the same way.
    """

    BINARY = ""

    def __init__(self, base_url: str, model: str, api_key: str = "",
                 session=None, timeout: int = 300, runner=subprocess.run):
        self.model = model
        self.timeout = timeout
        self._run = runner

    def _binary(self) -> str:
        return config.LLM_CLI_BINARY or self.BINARY

    def _command(self, text: str, system: str | None) -> list[str]:
        """Subclasses receive `text`, the prompt after _prompt_text has folded
        in any system prompt. `system` is passed through as well and is only
        useful to subclasses that have a dedicated system-prompt flag.
        """
        raise NotImplementedError

    def _prompt_text(self, prompt: str, system: str | None) -> str:
        """The prompt as the CLI will receive it.

        Claude takes the system prompt as a flag, so it leaves the text alone.
        Cursor and Codex document no equivalent and override this to fold it in.
        """
        return prompt

    def _stdin(self, text: str) -> str | None:
        """What to pipe. Cursor overrides to None — it takes the prompt in argv."""
        return text

    def _parse(self, stdout: str) -> str:
        """Codex uses this as-is: its stdout *is* the answer."""
        return stdout.strip()

    def generate(self, prompt: str, system: str | None = None) -> str:
        """Same contract as _HTTPClient.generate: never raises, "" on failure."""
        try:
            text = self._prompt_text(prompt, system)
            proc = self._run(
                self._command(text, system),
                input=self._stdin(text),
                capture_output=True,
                text=True,
                timeout=self.timeout,
                cwd=str(config.cli_workspace_dir()),
            )
            if proc.returncode != 0:
                log.info("LLM | %s exit %s: %s", self._binary(),
                         proc.returncode, (proc.stderr or "")[:200])
                return ""
            return self._parse(proc.stdout)
        except Exception as exc:
            log.info("LLM | generate failed: %s", exc)
            return ""


class ClaudeCLIClient(_CLIClient):
    """Claude Code CLI in headless mode — for a Pro/Max subscription."""

    BINARY = "claude"

    def _command(self, text, system):
        # --setting-sources and --strict-mcp-config keep a run launched from the
        # repo out of this project's CLAUDE.md and MCP servers: cost and latency
        # for four text-generation calls that need neither. --tools "" drops the
        # built-in tool definitions for the same reason — measured against the
        # real CLI it took the per-call prompt from ~17k tokens to ~6.4k.
        #
        # Never add --bare here. It looks right for a scripted run, but it
        # restricts auth to ANTHROPIC_API_KEY and never reads the OAuth login,
        # which is the only credential a subscription has.
        cmd = [self._binary(), "-p", "--output-format", "json",
               "--strict-mcp-config", "--setting-sources", "", "--tools", ""]
        if self.model:
            cmd += ["--model", self.model]
        if system:
            cmd += ["--append-system-prompt", system]
        return cmd

    def _parse(self, stdout):
        return _json_result(stdout, self._binary())


class CursorCLIClient(_CLIClient):
    """Cursor CLI in print mode — for a Cursor subscription.

    NOT VERIFIED against a live binary. Every flag and the output shape below
    come from Cursor's documentation, unlike ClaudeCLIClient which was built
    against the real CLI.

    Cursor emits the same `{is_error, result}` payload Claude Code does, so the
    parsing is shared. Two things differ and look like mistakes if you skim:
    the prompt goes in **argv**, because Cursor documents no stdin path; and the
    binary defaults to `cursor-agent`, which newer installs also expose as
    `agent` — set LLM_CLI_BINARY if yours is the short name.

    On Linux (the deployment target) the binding limit is MAX_ARG_STRLEN = 128 KiB
    per single argument, not ARG_MAX. Real prompts land at 10-25 KB, so the
    margin is ~5-10x. On macOS ARG_MAX is ~1 MB (a ~50x margin), but that is
    not the deployment target.
    """

    BINARY = "cursor-agent"

    def _command(self, text, system):
        # `system` is already folded into `text` by _prompt_text — Cursor
        # documents no --append-system-prompt equivalent. Do not re-add it here.
        #
        # --mode ask is the closest available equivalent to Claude's --tools "".
        # Unlike --tools "", which removes tool definitions from the prompt,
        # --mode ask restricts writes while leaving the model able to read the
        # filesystem and still paying for the tool definitions. The ~17k→6.4k
        # per-call prompt reduction measured for Claude does not transfer here.
        # --force/--yolo must never appear — the pipeline generates text only.
        cmd = [self._binary(), "-p", "--output-format", "json",
               "--mode", "ask",
               "--workspace", str(config.cli_workspace_dir())]
        if self.model:
            cmd += ["--model", self.model]
        cmd += ["--", text]
        return cmd

    def _prompt_text(self, prompt, system):
        return _with_system(prompt, system)

    def _stdin(self, text):
        return None

    def _parse(self, stdout):
        return _json_result(stdout, self._binary())


class CodexCLIClient(_CLIClient):
    """Codex CLI in non-interactive mode — for a ChatGPT subscription.

    NOT VERIFIED against a live binary; the flags come from OpenAI's docs.

    The simplest of the three: `codex exec` streams progress to stderr and
    prints only the final agent message to stdout, so the base `_parse` is
    already correct and there is no JSON to unpack. Never add --json — it turns
    stdout into a JSONL event stream and breaks exactly that.
    """

    BINARY = "codex"

    def _command(self, text, system):
        # --sandbox read-only is the closest available equivalent to Claude's
        # --tools "": codex exec has no flag to remove tool definitions, so this
        # restricts writes rather than removing them. The ~17k→6.4k per-call
        # prompt reduction measured for Claude does not transfer here.
        # --skip-git-repo-check because the workspace is a bare directory, and
        # --cd keeps AGENTS.md out of the prompt.
        cmd = [self._binary(), "exec",
               "--sandbox", "read-only",
               "--skip-git-repo-check",
               "--cd", str(config.cli_workspace_dir())]
        if self.model:
            cmd += ["-m", self.model]
        cmd.append("-")  # force reading the prompt from stdin
        return cmd

    def _prompt_text(self, prompt, system):
        return _with_system(prompt, system)


_PROVIDERS = {
    "ollama": OllamaClient,
    "openai-compatible": OpenAICompatClient,
    "anthropic": AnthropicClient,
    "claude-cli": ClaudeCLIClient,
    "cursor-cli": CursorCLIClient,
    "codex-cli": CodexCLIClient,
}


def _needs_api_key(cls) -> bool:
    """Ollama is local; every CLI provider carries its own credential.

    A predicate rather than a tuple so the next CLI provider is exempt by
    construction and cannot forget to register itself.
    """
    return not (cls is OllamaClient or issubclass(cls, _CLIClient))


# Per-provider hints for the "LLM_MODEL is required" error.
_MODEL_HINTS = {
    "cursor-cli": (
        f"run `{config.LLM_CLI_BINARY or 'cursor-agent'} --list-models`"
        " to see the ones your plan has"
    ),
}


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
        hint = _MODEL_HINTS.get(provider, "for example LLM_MODEL=gpt-4o-mini")
        raise ValueError(
            f"LLM_MODEL is required for LLM_PROVIDER={provider!r}. "
            f"Set it in .env ({hint})."
        )
    if _needs_api_key(cls) and not config.LLM_API_KEY:
        raise ValueError(
            f"LLM_API_KEY is required for LLM_PROVIDER={provider!r}. Set it in .env."
        )
    return cls(
        config.LLM_BASE_URL,
        config.LLM_MODEL,
        api_key=config.LLM_API_KEY,
        session=session,
    )
