# Subscription CLI Providers (Cursor + Codex) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a $20 ChatGPT or Cursor subscription run the trading pipeline, the way `claude-cli` already lets a Claude Pro/Max subscription run it.

**Architecture:** Extract a `_CLIClient` base from the existing `ClaudeCLIClient`, mirroring how `_HTTPClient` already backs the three HTTP providers. The base owns the subprocess transport (argv, stdin, timeout, exit codes, the "never raise, return `""`" contract); each subclass supplies argv shape, prompt routing and parsing. Cursor's JSON output is byte-identical to Claude's, so they share a parser; Codex prints only its final message to stdout, so it needs no parser at all.

**Tech Stack:** Python 3.10+, `subprocess`, pytest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-08-20-subscription-cli-providers-design.md`

## Global Constraints

- **The 12 existing `claude_cli` tests in `tests/test_llm.py` must pass unchanged.** The only permitted edit to existing test code in this plan is adding a `cwd=None` kwarg to `FakeRunner.__call__` (Task 1, Step 1). If a refactor needs more than that, it has gone wrong — stop and report.
- `generate()` **never raises**. Every failure path returns `""` and logs. A broken backend must fall through to no-trade, not abort a scheduled run midway through a portfolio.
- `build_client()` **raises** on misconfiguration. A run that cannot reach an LLM must stop before fetching market data, or the pipeline reads an empty analyst report as a genuine "no signal".
- Never pass `--bare` to `claude` — it restricts auth to `ANTHROPIC_API_KEY` and ignores the OAuth login, the only credential a subscription has.
- Never pass `--force` or `--yolo` to `cursor-agent`, and never take `codex` out of `--sandbox read-only`. The pipeline only generates text.
- Never add `--json` to the `codex exec` command — it turns stdout into a JSONL event stream and breaks the stdout-is-the-answer contract.
- Both new providers are written from vendor docs, **not verified against a live binary**. That caveat must appear in the module docstrings, the README, and the commit messages.
- Run tests with `.venv/bin/pytest`.
- New provider IDs are exactly `cursor-cli` and `codex-cli`.

---

### Task 1: Extract the `_CLIClient` base

The refactor. `ClaudeCLIClient` keeps its exact behaviour and gains two things it did not have: a configurable binary name and an isolated working directory.

**Files:**
- Modify: `config.py` (add `LLM_CLI_BINARY`, add `cli_workspace_dir()`)
- Modify: `analysis/llm.py:138-216` (replace `ClaudeCLIClient` and `_NO_API_KEY_NEEDED`)
- Test: `tests/test_llm.py`, `tests/test_config.py`

**Interfaces:**
- Consumes: `config.LLM_PROVIDER`, `config.LLM_MODEL`, `config.LLM_API_KEY` (existing)
- Produces:
  - `config.LLM_CLI_BINARY: str` — `""` when unset
  - `config.cli_workspace_dir() -> Path` — creates and returns an empty dir
  - `analysis.llm._with_system(prompt: str, system: str | None) -> str`
  - `analysis.llm._json_result(stdout: str) -> str`
  - `analysis.llm._CLIClient` with `BINARY: str`, `_binary() -> str`, `_command(text, system) -> list[str]`, `_prompt_text(prompt, system) -> str`, `_stdin(text) -> str | None`, `_parse(stdout) -> str`, `generate(prompt, system=None) -> str`. Note the first argument to `_command` and `_stdin` is `text` — the prompt *after* `_prompt_text` has folded in any system prompt.
  - `analysis.llm._needs_api_key(cls) -> bool`

- [ ] **Step 1: Add the `cwd` kwarg to `FakeRunner`, then confirm the suite is green**

This is the one permitted edit to existing test code. In `tests/test_llm.py:141`, replace the `__call__` signature:

```python
    def __call__(self, cmd, input=None, capture_output=None, text=None,
                 timeout=None, cwd=None):
        self.cmd = cmd
        self.stdin = input
        self.timeout = timeout
        self.cwd = cwd
        return types.SimpleNamespace(
            returncode=self._returncode, stdout=self._stdout, stderr=self._stderr
        )
```

Also add `self.cwd = None` to `FakeRunner.__init__`, after `self.timeout = None`.

Run: `.venv/bin/pytest tests/test_llm.py -v`
Expected: all 30 tests PASS. This is the baseline — record it.

- [ ] **Step 2: Write the failing tests for the new config surface**

Append to `tests/test_config.py`:

```python
def test_cli_workspace_dir_is_created_and_empty_of_agent_config(monkeypatch, tmp_path):
    """Codex reads AGENTS.md and Cursor reads .cursor/rules from the cwd.

    Pointing them at an empty directory is what keeps a cron run launched from
    this repo out of the repo's own agent instructions.
    """
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    d = config.cli_workspace_dir()
    assert d.is_dir()
    assert list(d.iterdir()) == []


def test_llm_cli_binary_defaults_to_empty(monkeypatch):
    monkeypatch.delenv("LLM_CLI_BINARY", raising=False)
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **kw: None)
    assert importlib.reload(config).LLM_CLI_BINARY == ""
```

- [ ] **Step 3: Run them to verify they fail**

Run: `.venv/bin/pytest tests/test_config.py -k "cli_workspace or cli_binary" -v`
Expected: FAIL — `AttributeError: module 'config' has no attribute 'cli_workspace_dir'`

- [ ] **Step 4: Add the config surface**

In `config.py`, after the `LLM_MODEL` assignment (line 45):

```python
# Overrides the binary name for any CLI provider; also accepts an absolute path.
# Not polish: Cursor renamed its binary from `cursor-agent` to `agent` and kept
# both working, so which name exists depends on how old the install is.
LLM_CLI_BINARY = os.getenv("LLM_CLI_BINARY", "").strip()
```

And after `agent_log_dir()` (line 73):

```python
def cli_workspace_dir() -> Path:
    """An empty directory for the subscription-CLI providers to run in.

    Codex reads AGENTS.md and Cursor reads .cursor/rules from their working
    directory. A cron run launched from this repo would otherwise drag
    trading-agent instructions into four text-generation calls that need none of
    it — cost, latency, and a real risk of the CLI trying to act on them.
    Neither CLI has a flag that disables project context, so they get pointed
    somewhere with none. User-global config (~/.codex/AGENTS.md) still applies.
    """
    d = Path.home() / ".stockbeat-agent" / "cli-workspace"
    d.mkdir(parents=True, exist_ok=True)
    return d
```

- [ ] **Step 5: Run the config tests to verify they pass**

Run: `.venv/bin/pytest tests/test_config.py -v`
Expected: all PASS

- [ ] **Step 6: Write the failing tests for the base-class behaviour**

Append to `tests/test_llm.py`, in the Claude CLI section (after `test_claude_cli_passes_timeout_to_runner`):

```python
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
```

No new imports are needed — `config` is already imported at the top of the file, and `generate` resolves `config.cli_workspace_dir` at call time, so patching the module attribute works.

- [ ] **Step 7: Run them to verify they fail**

Run: `.venv/bin/pytest tests/test_llm.py -k "isolated_workspace or binary_can_be_overridden" -v`
Expected: FAIL — `runner.cwd` is `None`, and `runner.cmd[0]` is `"claude"`

- [ ] **Step 8: Replace `ClaudeCLIClient` with the base plus subclass**

In `analysis/llm.py`, replace the whole `ClaudeCLIClient` class (lines 138-204) with:

```python
def _with_system(prompt: str, system: str | None) -> str:
    """Fold the system prompt into the text, for CLIs with no flag for it."""
    return f"{system}\n\n{prompt}" if system else prompt


def _json_result(stdout: str) -> str:
    """Parse the `{is_error, result}` payload Claude Code and Cursor both emit.

    An API error can still parse as JSON, and `subtype` reads "success" even
    then — `is_error` is the only honest signal. Its `result` is an English
    apology, so returning it unchecked would hand the pipeline an apology as if
    it were an analyst report.
    """
    payload = json.loads(stdout)
    if payload.get("is_error"):
        log.info("LLM | CLI reported an error (status %s): %s",
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
        return _json_result(stdout)
```

The prompt still goes on stdin, not argv — an analyst report plus a bull/bear exchange runs well past a comfortable argument length — which is now the base class's default `_stdin`.

- [ ] **Step 9: Replace `_NO_API_KEY_NEEDED` with a predicate**

In `analysis/llm.py`, replace lines 214-216 and the check inside `build_client`:

```python
def _needs_api_key(cls) -> bool:
    """Ollama is local; every CLI provider carries its own credential.

    A predicate rather than a tuple so the next CLI provider is exempt by
    construction and cannot forget to register itself.
    """
    return not (cls is OllamaClient or issubclass(cls, _CLIClient))
```

And in `build_client`, replace `if cls not in _NO_API_KEY_NEEDED and not config.LLM_API_KEY:` with:

```python
    if _needs_api_key(cls) and not config.LLM_API_KEY:
```

- [ ] **Step 10: Run the full LLM suite**

Run: `.venv/bin/pytest tests/test_llm.py -v`
Expected: all PASS, including the 12 pre-existing `claude_cli` tests **with no edits beyond Step 1**. If any of those 12 needed changing, stop and report.

- [ ] **Step 11: Run the whole suite to catch anything that imported the old names**

Run: `.venv/bin/pytest -q`
Expected: PASS (same count as before, plus the 4 new tests)

- [ ] **Step 12: Commit**

```bash
git add config.py analysis/llm.py tests/test_llm.py tests/test_config.py
git commit -m "refactor: extract _CLIClient base from ClaudeCLIClient

Mirrors how _HTTPClient backs the three HTTP providers. The subprocess
transport, timeout/exit-code handling and the never-raise contract move
to the base; ClaudeCLIClient keeps only its argv and JSON parsing.

Behaviour is unchanged: all 12 existing claude-cli tests pass untouched.
Claude also picks up the two things the base adds for its coming
siblings — a configurable binary name and an isolated working directory.

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 2: The `cursor-cli` provider

**Files:**
- Modify: `analysis/llm.py` (add `CursorCLIClient`, register in `_PROVIDERS`)
- Modify: `config.py` (add `cursor-cli` to `_PROVIDER_DEFAULTS`, add the model hint)
- Test: `tests/test_llm.py`

**Interfaces:**
- Consumes: `_CLIClient`, `_json_result`, `_with_system`, `config.cli_workspace_dir` (Task 1)
- Produces: `analysis.llm.CursorCLIClient` with `BINARY = "cursor-agent"`; provider id `"cursor-cli"`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_llm.py`, after the Claude CLI section. Note `CursorCLIClient` must be added to the `from analysis.llm import (...)` block at the top.

```python
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
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/pytest tests/test_llm.py -k cursor -v`
Expected: FAIL at import — `ImportError: cannot import name 'CursorCLIClient'`

- [ ] **Step 3: Add `CursorCLIClient`**

In `analysis/llm.py`, after `ClaudeCLIClient`:

```python
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
    """

    BINARY = "cursor-agent"

    def _command(self, text, system):
        # --mode ask is Cursor's equivalent of Claude's --tools "": read-only
        # Q&A, no file edits. --force/--yolo must never appear here — the
        # pipeline generates text and has no business touching a filesystem.
        cmd = [self._binary(), "-p", "--output-format", "json",
               "--mode", "ask",
               "--workspace", str(config.cli_workspace_dir())]
        if self.model:
            cmd += ["--model", self.model]
        cmd.append(text)
        return cmd

    def _prompt_text(self, prompt, system):
        return _with_system(prompt, system)

    def _stdin(self, text):
        return None

    def _parse(self, stdout):
        return _json_result(stdout)
```

Register it in `_PROVIDERS`:

```python
    "cursor-cli": CursorCLIClient,
```

- [ ] **Step 4: Add the config default and the model hint**

In `config.py`, add to `_PROVIDER_DEFAULTS`:

```python
    # No model default on purpose: Cursor's model IDs vary by account, and
    # LLM_MODEL is what gets stamped onto every trade as `llm_model`. Guessing
    # would either fail mid-run or record a lie about which model decided.
    "cursor-cli": ("", ""),
```

In `analysis/llm.py`, above `build_client`:

```python
# Per-provider hints for the "LLM_MODEL is required" error.
_MODEL_HINTS = {
    "cursor-cli": "run `cursor-agent --list-models` to see the ones your plan has",
}
```

And in `build_client`, replace the `LLM_MODEL` check body:

```python
    if not config.LLM_MODEL:
        hint = _MODEL_HINTS.get(provider, "for example LLM_MODEL=gpt-4o-mini")
        raise ValueError(
            f"LLM_MODEL is required for LLM_PROVIDER={provider!r}. "
            f"Set it in .env ({hint})."
        )
```

The existing `test_build_client_missing_model_raises` matches on `"LLM_MODEL is required"`, which this preserves.

- [ ] **Step 5: Run the Cursor tests**

Run: `.venv/bin/pytest tests/test_llm.py -k cursor -v`
Expected: all PASS

- [ ] **Step 6: Run the whole suite**

Run: `.venv/bin/pytest -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add analysis/llm.py config.py tests/test_llm.py
git commit -m "feat: add cursor-cli LLM provider for Cursor subscriptions

Cursor's print mode emits the same {is_error, result} payload Claude
Code does, so it reuses the shared parser. Two documented differences:
the prompt goes in argv, and --mode ask is its read-only equivalent of
Claude's --tools \"\".

Written from Cursor's docs — not yet verified against a live binary.

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 3: The `codex-cli` provider

**Files:**
- Modify: `analysis/llm.py` (add `CodexCLIClient`, register in `_PROVIDERS`)
- Modify: `config.py` (add `codex-cli` to `_PROVIDER_DEFAULTS`)
- Test: `tests/test_llm.py`

**Interfaces:**
- Consumes: `_CLIClient`, `_with_system`, `config.cli_workspace_dir` (Task 1)
- Produces: `analysis.llm.CodexCLIClient` with `BINARY = "codex"`; provider id `"codex-cli"`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_llm.py`. Add `CodexCLIClient` to the import block.

```python
# --- Codex CLI (ChatGPT subscription, no API key) ---
# Documented behaviour only; not checked against a live binary.

def test_codex_cli_returns_stdout_verbatim():
    """codex exec streams progress to stderr and prints only the final agent
    message to stdout, so stdout *is* the answer — there is nothing to parse."""
    runner = FakeRunner(stdout="codex says\n", stderr="thinking...\ntool call\n")
    client = CodexCLIClient("", "gpt-5.1-codex-max", runner=runner)
    assert client.generate("hi") == "codex says"
    assert runner.cmd[0] == "codex"
    assert runner.cmd[1] == "exec"
    assert flag_value(runner.cmd, "-m") == "gpt-5.1-codex-max"


def test_codex_cli_non_json_stdout_is_a_success_not_a_parse_failure():
    """The inverse of the Claude/Cursor trap: prose here is the expected shape.

    Adding --json would turn stdout into a JSONL event stream and break this.
    """
    runner = FakeRunner(stdout="Prose, not JSON. Deliberately.")
    assert CodexCLIClient("", "m", runner=runner).generate("hi") == (
        "Prose, not JSON. Deliberately.")


def test_codex_cli_sends_prompt_on_stdin_not_argv():
    runner = FakeRunner(stdout="ok")
    CodexCLIClient("", "m", runner=runner).generate("a very long prompt")
    assert runner.stdin == "a very long prompt"
    assert "a very long prompt" not in runner.cmd
    # `-` forces reading the prompt from stdin.
    assert runner.cmd[-1] == "-"


def test_codex_cli_folds_system_into_the_prompt():
    """codex exec documents no --append-system-prompt equivalent."""
    runner = FakeRunner(stdout="ok")
    CodexCLIClient("", "m", runner=runner).generate("hi", system="be terse")
    assert runner.stdin == "be terse\n\nhi"


def test_codex_cli_runs_read_only_and_outside_a_repo():
    runner = FakeRunner(stdout="ok")
    CodexCLIClient("", "m", runner=runner).generate("hi")
    assert flag_value(runner.cmd, "--sandbox") == "read-only"
    assert "--skip-git-repo-check" in runner.cmd


def test_codex_cli_never_passes_json():
    """--json makes stdout a JSONL event stream, breaking the stdout contract."""
    runner = FakeRunner(stdout="ok")
    CodexCLIClient("", "m", runner=runner).generate("hi")
    assert "--json" not in runner.cmd


def test_codex_cli_points_cd_at_the_isolated_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "cli_workspace_dir", lambda: tmp_path)
    runner = FakeRunner(stdout="ok")
    CodexCLIClient("", "m", runner=runner).generate("hi")
    assert flag_value(runner.cmd, "--cd") == str(tmp_path)


def test_codex_cli_omits_model_flag_when_unset():
    runner = FakeRunner(stdout="ok")
    CodexCLIClient("", "", runner=runner).generate("hi")
    assert "-m" not in runner.cmd


def test_codex_cli_empty_on_nonzero_exit():
    runner = FakeRunner(stdout="", returncode=1, stderr="not logged in")
    assert CodexCLIClient("", "m", runner=runner).generate("hi") == ""


def test_codex_cli_empty_when_binary_missing():
    def boom(*a, **kw):
        raise FileNotFoundError("codex")

    assert CodexCLIClient("", "m", runner=boom).generate("hi") == ""


def test_codex_cli_empty_on_timeout():
    def boom(*a, **kw):
        raise subprocess.TimeoutExpired(cmd="codex", timeout=1)

    assert CodexCLIClient("", "m", runner=boom).generate("hi") == ""


def test_build_client_codex_cli_needs_no_api_key(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "codex-cli")
    monkeypatch.setattr(config, "LLM_BASE_URL", "")
    monkeypatch.setattr(config, "LLM_MODEL", "gpt-5.1-codex-max")
    monkeypatch.setattr(config, "LLM_API_KEY", "")
    client = build_client()
    assert isinstance(client, CodexCLIClient)
    assert client.model == "gpt-5.1-codex-max"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/pytest tests/test_llm.py -k codex -v`
Expected: FAIL at import — `ImportError: cannot import name 'CodexCLIClient'`

- [ ] **Step 3: Add `CodexCLIClient`**

In `analysis/llm.py`, after `CursorCLIClient`:

```python
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
        # read-only sandbox for the same reason Claude gets --tools "": the
        # pipeline generates text. --skip-git-repo-check because the workspace
        # is a bare directory, and --cd keeps AGENTS.md out of the prompt.
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
```

Register it in `_PROVIDERS`:

```python
    "codex-cli": CodexCLIClient,
```

- [ ] **Step 4: Add the config default**

In `config.py`, add to `_PROVIDER_DEFAULTS`:

```python
    "codex-cli": ("", "gpt-5.1-codex-max"),
```

- [ ] **Step 5: Cover both providers' config defaults**

Both `_PROVIDER_DEFAULTS` entries now exist, so pin them together. Append to `tests/test_config.py`:

```python
def test_cli_providers_take_no_base_url():
    """None of the CLI providers speak HTTP, so a base_url would be a lie."""
    for provider in ("claude-cli", "cursor-cli", "codex-cli"):
        base, _ = config._PROVIDER_DEFAULTS[provider]
        assert base == "", f"{provider} should have no default base URL"


def test_cursor_cli_has_no_default_model():
    """Cursor's model IDs vary by account and LLM_MODEL is stamped onto every
    trade, so build_client must demand an explicit one."""
    assert config._PROVIDER_DEFAULTS["cursor-cli"][1] == ""


def test_codex_cli_default_model_fits_the_llm_model_field():
    """StockBeat requires 2-30 chars matching [A-Za-z0-9 ._:/+()-]."""
    import re
    model = config._PROVIDER_DEFAULTS["codex-cli"][1]
    assert 2 <= len(model) <= 30
    assert re.fullmatch(r"[A-Za-z0-9 ._:/+()-]+", model)
```

Run: `.venv/bin/pytest tests/test_config.py -v`
Expected: all PASS

- [ ] **Step 6: Run the Codex tests**

Run: `.venv/bin/pytest tests/test_llm.py -k codex -v`
Expected: all PASS

- [ ] **Step 7: Run the whole suite**

Run: `.venv/bin/pytest -q`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add analysis/llm.py config.py tests/test_llm.py tests/test_config.py
git commit -m "feat: add codex-cli LLM provider for ChatGPT subscriptions

codex exec prints only its final message to stdout and streams progress
to stderr, so the base _CLIClient parser needs no override. --json is
deliberately absent: it would make stdout a JSONL event stream.

Written from OpenAI's docs — not yet verified against a live binary.

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 4: Documentation

**Files:**
- Modify: `README.md:62-94` (provider table + a section per new provider)
- Modify: `CLAUDE.md:126-171` (widen the `claude-cli` section to all three)
- Modify: `.env.example:15-26`

- [ ] **Step 1: Extend the README provider table**

In `README.md`, add two rows after the `claude-cli` row (line 72):

```markdown
| `cursor-cli` | not used | required, see below | not needed |
| `codex-cli` | not used | `gpt-5.1-codex-max` | not needed |
```

- [ ] **Step 2: Add the two provider sections**

In `README.md`, after the "Using a Claude Pro/Max subscription" section (after line 94):

````markdown
### Using a ChatGPT subscription

`LLM_PROVIDER=codex-cli` runs the pipeline through the [Codex](https://developers.openai.com/codex) CLI in non-interactive mode. Codex CLI usage is covered by a ChatGPT Plus/Pro plan under that plan's rate limits, so the four calls per run are not billed per token:

```bash
npm install -g @openai/codex
codex login                                # once, interactively — choose "Sign in with ChatGPT"
```

```
LLM_PROVIDER=codex-cli
LLM_MODEL=gpt-5.1-codex-max
```

Each call runs `codex exec --sandbox read-only` in an empty directory, so the CLI cannot edit files and does not read this repo's `AGENTS.md`.

### Using a Cursor subscription

`LLM_PROVIDER=cursor-cli` runs the pipeline through the [Cursor CLI](https://cursor.com/docs/cli/overview) in print mode:

```bash
curl https://cursor.com/install -fsS | bash
cursor-agent login                         # once, interactively
```

```
LLM_PROVIDER=cursor-cli
LLM_MODEL=composer-1                       # run `cursor-agent --list-models` for yours
```

There is no default model, on purpose: Cursor's model IDs vary by account, and `LLM_MODEL` is stamped onto every trade as `llm_model`, so a guessed default would record a lie about which model made the decision.

**Cursor's economics differ from the other two.** A Cursor plan includes a credit pool roughly equal to the subscription price, and CLI runs draw it down like API usage rather than being flat-rate. Four calls per agent per day can reach overages; a ChatGPT plan covering Codex under rate limits is the closer fit for a daily agent.

Cursor renamed its binary from `cursor-agent` to `agent` and kept both working. If yours is the short name, set `LLM_CLI_BINARY=agent`.

### Caveats for all three CLI providers

- They need a real interactive login, so **none of them work inside the Docker image**, and an unattended cron run will fail once the CLI's token expires and needs re-authentication.
- `LLM_CLI_BINARY` overrides the binary name or gives an absolute path, for any of them.
- **`cursor-cli` and `codex-cli` were written from vendor documentation and have not been verified against a live binary** — unlike `claude-cli`, which was built against the real CLI. If a flag has moved, `generate()` returns `""` and the run safely does nothing rather than trading on a broken response. Reports welcome.
````

- [ ] **Step 3: Update `.env.example`**

In `.env.example`, after line 26:

```
#   cursor-cli: LLM_PROVIDER=cursor-cli LLM_MODEL=composer-1  (cursor-agent --list-models)
#   codex-cli:  LLM_PROVIDER=codex-cli  LLM_MODEL=gpt-5.1-codex-max
#
# Overrides the CLI binary name or path for the *-cli providers. Cursor's is
# `agent` on newer installs and `cursor-agent` on older ones.
LLM_CLI_BINARY=
```

- [ ] **Step 4: Widen the CLAUDE.md section**

In `CLAUDE.md`, change line 130 to list the new providers, and retitle the "### The `claude-cli` provider" section to "### The subscription-CLI providers". Keep the existing Claude paragraphs verbatim — the `--bare` and `is_error` notes are load-bearing — and append:

```markdown
All three share `_CLIClient`, which owns the subprocess transport the way
`_HTTPClient` owns the HTTP one. Each subclass supplies argv, prompt routing
and parsing. `_needs_api_key` exempts every `_CLIClient` subclass from the
`LLM_API_KEY` check by construction, so the next CLI provider cannot forget to
register itself.

All three run with `cwd` set to `config.cli_workspace_dir()`, an empty
directory. Codex reads `AGENTS.md` and Cursor reads `.cursor/rules` from the
working directory; neither has a flag to disable that, so they get pointed
somewhere with nothing in it. User-global config still applies.

`cursor-cli` and `codex-cli` were written from vendor docs and **have not been
run against a live binary.** Three details there look wrong to a passing reader:

- **Cursor takes the prompt in argv, not stdin**, unlike its two siblings.
  Cursor documents no stdin path. macOS `ARG_MAX` is ~1MB, so a tens-of-KB
  analyst prompt fits.
- **Never add `--json` to `codex exec`.** `codex exec` streams progress to
  stderr and prints only the final message to stdout, which is why
  `CodexCLIClient` needs no parser. `--json` turns stdout into a JSONL event
  stream and breaks that.
- **Neither has a system-prompt flag**, so both fold `system` into the prompt
  text via `_with_system`. Only Claude uses `--append-system-prompt`.

The `is_error`-not-`subtype` trap applies to Cursor too — its payload is
byte-identical to Claude's, which is why `_json_result` is shared.
```

Also update the Environment Variables block: add `LLM_CLI_BINARY=` and change the closing note to "The `LLM_API_KEY` is not needed when `LLM_PROVIDER` is `ollama` or any of the `*-cli` providers; `LLM_BASE_URL` is unused by all of them."

- [ ] **Step 5: Verify the docs match the code**

Run: `.venv/bin/pytest -q && grep -c "cursor-cli\|codex-cli" README.md CLAUDE.md .env.example`
Expected: tests PASS, and every file reports a non-zero count.

- [ ] **Step 6: Commit**

```bash
git add README.md CLAUDE.md .env.example
git commit -m "docs: document the cursor-cli and codex-cli providers

Includes the cost asymmetry the provider table hides: a ChatGPT plan
covers Codex under rate limits, while a Cursor plan draws down a credit
pool and can reach overages on a daily agent.

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Verification

After Task 4:

1. `.venv/bin/pytest -q` — full suite green.
2. `.venv/bin/pytest tests/test_llm.py -k claude_cli -v` — the 12 original tests, still green, still unedited. Confirm with `git diff master -- tests/test_llm.py` that the only change inside those 12 is untouched.
3. Misconfiguration is loud:
   ```bash
   LLM_PROVIDER=cursor-cli LLM_MODEL= .venv/bin/python -c \
     "import config, analysis.llm as l; l.build_client()"
   ```
   Expected: `ValueError: LLM_MODEL is required for LLM_PROVIDER='cursor-cli'. Set it in .env (run `cursor-agent --list-models` ...)`
4. A missing binary is quiet and safe:
   ```bash
   LLM_PROVIDER=codex-cli .venv/bin/python -c \
     "import analysis.llm as l; print(repr(l.build_client().generate('hi')))"
   ```
   Expected: `''` — plus a log line — because `codex` is not installed here. This is the whole safety contract: an absent CLI produces no trade, not a crash.
5. **Not possible in this environment:** a live end-to-end run of either new provider. That requires an installed, logged-in `codex` or `cursor-agent` and the corresponding subscription. Until someone does it, the "not verified" wording in the README and module docstrings stays.
