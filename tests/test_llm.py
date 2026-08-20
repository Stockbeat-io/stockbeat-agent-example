import subprocess
import types

import pytest

import config
from analysis.llm import (
    AnthropicClient,
    ClaudeCLIClient,
    CursorCLIClient,
    OllamaClient,
    OpenAICompatClient,
    build_client,
)


class FakeResp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload


class FakeSession:
    """Records the last request. Note `headers` — the client always sends it now."""

    def __init__(self, resp):
        self.resp = resp
        self.last_url = None
        self.last_json = None
        self.last_headers = None

    def post(self, url, json=None, headers=None, timeout=None):
        self.last_url = url
        self.last_json = json
        self.last_headers = headers
        return self.resp


class BoomSession:
    def post(self, url, json=None, headers=None, timeout=None):
        raise RuntimeError("backend down")


# --- Ollama ---

def test_ollama_returns_content():
    sess = FakeSession(FakeResp({"message": {"content": "hello"}}))
    client = OllamaClient("http://x", "mistral:7b", session=sess)
    assert client.generate("hi", system="be terse") == "hello"
    assert sess.last_url == "http://x/api/chat"
    assert sess.last_json["model"] == "mistral:7b"
    assert sess.last_json["stream"] is False
    assert [m["role"] for m in sess.last_json["messages"]] == ["system", "user"]


def test_ollama_without_system_omits_system_message():
    sess = FakeSession(FakeResp({"message": {"content": "ok"}}))
    OllamaClient("http://x", "mistral:7b", session=sess).generate("hi")
    assert [m["role"] for m in sess.last_json["messages"]] == ["user"]


def test_ollama_empty_on_error():
    assert OllamaClient("http://x", "m", session=BoomSession()).generate("hi") == ""


# --- OpenAI-compatible ---

def test_openai_compat_returns_content():
    payload = {"choices": [{"message": {"content": "hi there"}}]}
    sess = FakeSession(FakeResp(payload))
    client = OpenAICompatClient("http://y", "gpt-4o-mini", api_key="k1", session=sess)
    assert client.generate("hi", system="sys") == "hi there"
    assert sess.last_url == "http://y/v1/chat/completions"
    assert sess.last_headers["Authorization"] == "Bearer k1"
    assert [m["role"] for m in sess.last_json["messages"]] == ["system", "user"]


def test_openai_compat_strips_trailing_slash_on_base_url():
    sess = FakeSession(FakeResp({"choices": [{"message": {"content": "x"}}]}))
    OpenAICompatClient("http://y/", "m", api_key="k", session=sess).generate("hi")
    assert sess.last_url == "http://y/v1/chat/completions"


def test_openai_compat_empty_on_error():
    client = OpenAICompatClient("http://y", "m", api_key="k", session=BoomSession())
    assert client.generate("hi") == ""


# --- Anthropic ---

def test_anthropic_returns_content():
    sess = FakeSession(FakeResp({"content": [{"text": "claude says"}]}))
    client = AnthropicClient("http://z", "claude-sonnet-4-6", api_key="k2", session=sess)
    assert client.generate("hi", system="sys") == "claude says"
    assert sess.last_url == "http://z/v1/messages"
    assert sess.last_headers["x-api-key"] == "k2"
    assert sess.last_headers["anthropic-version"] == "2023-06-01"
    assert sess.last_json["max_tokens"] > 0


def test_anthropic_system_is_top_level_not_a_message():
    """The Messages API takes `system` as a top-level field, not a role."""
    sess = FakeSession(FakeResp({"content": [{"text": "x"}]}))
    AnthropicClient("http://z", "m", api_key="k", session=sess).generate("hi", system="be terse")
    assert sess.last_json["system"] == "be terse"
    assert [m["role"] for m in sess.last_json["messages"]] == ["user"]


def test_anthropic_omits_system_when_absent():
    sess = FakeSession(FakeResp({"content": [{"text": "x"}]}))
    AnthropicClient("http://z", "m", api_key="k", session=sess).generate("hi")
    assert "system" not in sess.last_json


def test_anthropic_empty_on_error():
    client = AnthropicClient("http://z", "m", api_key="k", session=BoomSession())
    assert client.generate("hi") == ""


def test_malformed_response_returns_empty():
    """A 200 with an unexpected shape must not raise into the pipeline."""
    sess = FakeSession(FakeResp({"unexpected": "shape"}))
    assert OllamaClient("http://x", "m", session=sess).generate("hi") == ""


# --- Claude CLI (Pro/Max subscription, no API key) ---

class FakeRunner:
    """Stands in for subprocess.run. Records argv and stdin."""

    def __init__(self, stdout='{"result": "cli says"}', returncode=0, stderr=""):
        self._stdout = stdout
        self._returncode = returncode
        self._stderr = stderr
        self.cmd = None
        self.stdin = None
        self.timeout = None
        self.cwd = None

    def __call__(self, cmd, input=None, capture_output=None, text=None,
                 timeout=None, cwd=None):
        self.cmd = cmd
        self.stdin = input
        self.timeout = timeout
        self.cwd = cwd
        return types.SimpleNamespace(
            returncode=self._returncode, stdout=self._stdout, stderr=self._stderr
        )


def flag_value(cmd, flag):
    """Return the argument following `flag`, or None if the flag is absent."""
    return cmd[cmd.index(flag) + 1] if flag in cmd else None


def test_claude_cli_returns_result_field():
    runner = FakeRunner()
    client = ClaudeCLIClient("", "claude-opus-4-8", runner=runner)
    assert client.generate("hi", system="be terse") == "cli says"
    assert runner.cmd[0] == "claude"
    assert "-p" in runner.cmd
    assert flag_value(runner.cmd, "--output-format") == "json"
    assert flag_value(runner.cmd, "--model") == "claude-opus-4-8"


def test_claude_cli_sends_prompt_on_stdin_not_argv():
    """Analyst reports are far too long for argv; they must go on stdin."""
    runner = FakeRunner()
    ClaudeCLIClient("", "m", runner=runner).generate("a very long prompt")
    assert runner.stdin == "a very long prompt"
    assert "a very long prompt" not in runner.cmd


def test_claude_cli_passes_system_as_append_system_prompt():
    runner = FakeRunner()
    ClaudeCLIClient("", "m", runner=runner).generate("hi", system="be terse")
    assert flag_value(runner.cmd, "--append-system-prompt") == "be terse"


def test_claude_cli_omits_system_flag_when_absent():
    runner = FakeRunner()
    ClaudeCLIClient("", "m", runner=runner).generate("hi")
    assert "--append-system-prompt" not in runner.cmd


def test_claude_cli_isolates_from_repo_settings():
    """A cron run inside the repo must not inherit CLAUDE.md or MCP servers."""
    runner = FakeRunner()
    ClaudeCLIClient("", "m", runner=runner).generate("hi")
    assert "--strict-mcp-config" in runner.cmd
    assert flag_value(runner.cmd, "--setting-sources") == ""


def test_claude_cli_never_passes_bare():
    """--bare forces API-key auth and ignores the OAuth login entirely."""
    runner = FakeRunner()
    ClaudeCLIClient("", "m", runner=runner).generate("hi")
    assert "--bare" not in runner.cmd


def test_claude_cli_disables_tools():
    """The pipeline only generates text; tool definitions are dead prompt weight.

    Measured against the real CLI, dropping them cut the per-call prompt from
    ~17k tokens to ~6.4k.
    """
    runner = FakeRunner()
    ClaudeCLIClient("", "m", runner=runner).generate("hi")
    assert flag_value(runner.cmd, "--tools") == ""


def test_claude_cli_empty_when_is_error_set():
    """An API error still yields exit 0 + parseable JSON on some paths.

    `result` then holds an English apology, and `subtype` is *still* "success",
    so `is_error` is the only trustworthy signal. Returning that prose would
    feed the pipeline an apology as if it were an analyst report.
    """
    runner = FakeRunner(
        stdout='{"subtype": "success", "is_error": true,'
               ' "result": "There is an issue with the selected model."}'
    )
    assert ClaudeCLIClient("", "m", runner=runner).generate("hi") == ""


def test_claude_cli_empty_on_nonzero_exit():
    runner = FakeRunner(stdout="", returncode=1, stderr="not logged in")
    assert ClaudeCLIClient("", "m", runner=runner).generate("hi") == ""


def test_claude_cli_empty_on_timeout():
    def boom(*a, **kw):
        raise subprocess.TimeoutExpired(cmd="claude", timeout=1)

    assert ClaudeCLIClient("", "m", runner=boom).generate("hi") == ""


def test_claude_cli_empty_when_binary_missing():
    def boom(*a, **kw):
        raise FileNotFoundError("claude")

    assert ClaudeCLIClient("", "m", runner=boom).generate("hi") == ""


def test_claude_cli_empty_on_non_json_output():
    """A CLI that printed a warning instead of JSON must not raise."""
    runner = FakeRunner(stdout="Usage: claude [options]")
    assert ClaudeCLIClient("", "m", runner=runner).generate("hi") == ""


def test_claude_cli_empty_on_json_without_result():
    runner = FakeRunner(stdout='{"unexpected": "shape"}')
    assert ClaudeCLIClient("", "m", runner=runner).generate("hi") == ""


def test_claude_cli_passes_timeout_to_runner():
    runner = FakeRunner()
    ClaudeCLIClient("", "m", runner=runner, timeout=300).generate("hi")
    assert runner.timeout == 300


def test_cli_client_runs_in_an_isolated_workspace(monkeypatch, tmp_path):
    """Codex and Cursor read agent config from the cwd; Claude inherits the
    same isolation for free once the transport is shared."""
    monkeypatch.setattr(config, "cli_workspace_dir", lambda: tmp_path)
    runner = FakeRunner()
    ClaudeCLIClient("", "m", runner=runner).generate("hi")
    assert runner.cwd == str(tmp_path)


def test_cli_binary_can_be_overridden(monkeypatch):
    """Cursor's binary is `agent` on new installs and `cursor-agent` on old
    ones, so the name cannot be hard-coded."""
    monkeypatch.setattr(config, "LLM_CLI_BINARY", "/opt/custom/claude")
    runner = FakeRunner()
    ClaudeCLIClient("", "m", runner=runner).generate("hi")
    assert runner.cmd[0] == "/opt/custom/claude"


# --- Factory ---

def test_build_client_defaults_to_ollama(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "ollama")
    monkeypatch.setattr(config, "LLM_BASE_URL", "http://localhost:11434")
    monkeypatch.setattr(config, "LLM_MODEL", "mistral:7b")
    monkeypatch.setattr(config, "LLM_API_KEY", "")
    client = build_client()
    assert isinstance(client, OllamaClient)
    assert client.model == "mistral:7b"


def test_build_client_ollama_needs_no_api_key(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "ollama")
    monkeypatch.setattr(config, "LLM_BASE_URL", "http://localhost:11434")
    monkeypatch.setattr(config, "LLM_MODEL", "mistral:7b")
    monkeypatch.setattr(config, "LLM_API_KEY", "")
    assert build_client() is not None


def test_build_client_anthropic(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "anthropic")
    monkeypatch.setattr(config, "LLM_BASE_URL", "https://api.anthropic.com")
    monkeypatch.setattr(config, "LLM_MODEL", "claude-sonnet-4-6")
    monkeypatch.setattr(config, "LLM_API_KEY", "sk-ant-test")
    assert isinstance(build_client(), AnthropicClient)


def test_build_client_unknown_provider_raises(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "gemini")
    monkeypatch.setattr(config, "LLM_MODEL", "m")
    monkeypatch.setattr(config, "LLM_API_KEY", "k")
    with pytest.raises(ValueError, match="Unknown LLM_PROVIDER"):
        build_client()


def test_build_client_missing_model_raises(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "openai-compatible")
    monkeypatch.setattr(config, "LLM_BASE_URL", "https://api.openai.com")
    monkeypatch.setattr(config, "LLM_MODEL", "")
    monkeypatch.setattr(config, "LLM_API_KEY", "k")
    with pytest.raises(ValueError, match="LLM_MODEL is required"):
        build_client()


def test_build_client_claude_cli(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "claude-cli")
    monkeypatch.setattr(config, "LLM_BASE_URL", "")
    monkeypatch.setattr(config, "LLM_MODEL", "claude-opus-4-8")
    monkeypatch.setattr(config, "LLM_API_KEY", "")
    client = build_client()
    assert isinstance(client, ClaudeCLIClient)
    assert client.model == "claude-opus-4-8"


def test_build_client_claude_cli_needs_no_api_key(monkeypatch):
    """The subscription credential lives in the CLI, so there is no key to set."""
    monkeypatch.setattr(config, "LLM_PROVIDER", "claude-cli")
    monkeypatch.setattr(config, "LLM_BASE_URL", "")
    monkeypatch.setattr(config, "LLM_MODEL", "claude-opus-4-8")
    monkeypatch.setattr(config, "LLM_API_KEY", "")
    assert build_client() is not None


def test_build_client_missing_api_key_raises(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "anthropic")
    monkeypatch.setattr(config, "LLM_BASE_URL", "https://api.anthropic.com")
    monkeypatch.setattr(config, "LLM_MODEL", "claude-sonnet-4-6")
    monkeypatch.setattr(config, "LLM_API_KEY", "")
    with pytest.raises(ValueError, match="LLM_API_KEY is required"):
        build_client()


# --- Cursor CLI (Cursor subscription, no API key) ---
# Every assertion here encodes Cursor's *documented* behaviour. Nothing in this
# section has been checked against a live binary.

def test_cursor_cli_returns_result_field():
    runner = FakeRunner(
        stdout='{"type": "result", "subtype": "success", "is_error": false,'
               ' "result": "cursor says", "session_id": "abc"}'
    )
    client = CursorCLIClient("", "composer-1", runner=runner)
    assert client.generate("hi") == "cursor says"
    assert runner.cmd[0] == "cursor-agent"
    assert "-p" in runner.cmd
    assert flag_value(runner.cmd, "--output-format") == "json"
    assert flag_value(runner.cmd, "--model") == "composer-1"


def test_cursor_cli_sends_prompt_on_argv_not_stdin():
    """Cursor documents prompts as arguments only and says nothing about stdin.

    macOS ARG_MAX is ~1MB, so a tens-of-KB analyst prompt fits comfortably.
    Following the only documented behaviour beats a uniform contract that
    cannot be tested here.
    """
    runner = FakeRunner(stdout='{"is_error": false, "result": "x"}')
    CursorCLIClient("", "m", runner=runner).generate("a very long prompt")
    assert runner.cmd[-1] == "a very long prompt"
    assert runner.stdin is None


def test_cursor_cli_folds_system_into_the_prompt():
    """Cursor documents no --append-system-prompt equivalent."""
    runner = FakeRunner(stdout='{"is_error": false, "result": "x"}')
    CursorCLIClient("", "m", runner=runner).generate("hi", system="be terse")
    assert runner.cmd[-1] == "be terse\n\nhi"


def test_cursor_cli_runs_read_only():
    """--mode ask is Cursor's --tools "": Q&A, no edits. The pipeline only
    generates text, so --force/--yolo must never appear."""
    runner = FakeRunner(stdout='{"is_error": false, "result": "x"}')
    CursorCLIClient("", "m", runner=runner).generate("hi")
    assert flag_value(runner.cmd, "--mode") == "ask"
    assert "--force" not in runner.cmd
    assert "--yolo" not in runner.cmd


def test_cursor_cli_points_workspace_at_the_isolated_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "cli_workspace_dir", lambda: tmp_path)
    runner = FakeRunner(stdout='{"is_error": false, "result": "x"}')
    CursorCLIClient("", "m", runner=runner).generate("hi")
    assert flag_value(runner.cmd, "--workspace") == str(tmp_path)


def test_cursor_cli_omits_model_flag_when_unset():
    runner = FakeRunner(stdout='{"is_error": false, "result": "x"}')
    CursorCLIClient("", "", runner=runner).generate("hi")
    assert "--model" not in runner.cmd


def test_cursor_cli_empty_when_is_error_set():
    """Same trap as Claude: `subtype` still reads "success" on an API error."""
    runner = FakeRunner(
        stdout='{"subtype": "success", "is_error": true,'
               ' "result": "Sorry, something went wrong."}'
    )
    assert CursorCLIClient("", "m", runner=runner).generate("hi") == ""


def test_cursor_cli_empty_on_nonzero_exit():
    runner = FakeRunner(stdout="", returncode=1, stderr="not logged in")
    assert CursorCLIClient("", "m", runner=runner).generate("hi") == ""


def test_cursor_cli_empty_on_non_json_output():
    runner = FakeRunner(stdout="Usage: cursor-agent [options]")
    assert CursorCLIClient("", "m", runner=runner).generate("hi") == ""


def test_cursor_cli_empty_when_binary_missing():
    def boom(*a, **kw):
        raise FileNotFoundError("cursor-agent")

    assert CursorCLIClient("", "m", runner=boom).generate("hi") == ""


def test_cursor_cli_empty_on_timeout():
    def boom(*a, **kw):
        raise subprocess.TimeoutExpired(cmd="cursor-agent", timeout=1)

    assert CursorCLIClient("", "m", runner=boom).generate("hi") == ""


def test_build_client_cursor_cli_needs_no_api_key(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "cursor-cli")
    monkeypatch.setattr(config, "LLM_BASE_URL", "")
    monkeypatch.setattr(config, "LLM_MODEL", "composer-1")
    monkeypatch.setattr(config, "LLM_API_KEY", "")
    client = build_client()
    assert isinstance(client, CursorCLIClient)
    assert client.model == "composer-1"


def test_build_client_cursor_cli_requires_an_explicit_model(monkeypatch):
    """Cursor's model IDs vary by account, and LLM_MODEL is stamped onto every
    trade as `llm_model`. A guessed default would record a lie about which
    model made a trading decision, so there is no default."""
    monkeypatch.setattr(config, "LLM_PROVIDER", "cursor-cli")
    monkeypatch.setattr(config, "LLM_BASE_URL", "")
    monkeypatch.setattr(config, "LLM_MODEL", "")
    monkeypatch.setattr(config, "LLM_API_KEY", "")
    with pytest.raises(ValueError, match="--list-models"):
        build_client()
