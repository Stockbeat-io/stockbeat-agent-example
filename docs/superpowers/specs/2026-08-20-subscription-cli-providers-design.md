# Subscription CLI providers: Cursor and Codex

**Date:** 2026-08-20
**Status:** Approved, not yet implemented

## Context

The agent makes four LLM calls per run, per agent, every day. Paid HTTP providers
bill all four per token, which is why Ollama is the default. `claude-cli` was
added as the escape hatch: it shells out to an already-logged-in Claude Code CLI
so a flat-rate Pro/Max subscription covers the run with no API key at all.

Users with a $20 ChatGPT or Cursor subscription have the same flat-rate
credential and no way to use it. This adds `codex-cli` and `cursor-cli` as
siblings of `claude-cli`.

The intended outcome: three subscription CLIs behind one interface, sharing the
subprocess plumbing the way the three HTTP providers already share
`_HTTPClient`, with the existing `claude-cli` behaviour provably unchanged.

### What is verified and what is not

Every flag and output shape below comes from vendor documentation, not from a
run against a real binary. Neither `codex` nor `cursor-agent` is installed on the
machine where this was designed. The `claude-cli` provider, by contrast, was
built against the real CLI — its token measurements in CLAUDE.md are observed
numbers.

**Both new providers ship marked as unverified** — in the README, in the module,
and in the commit message. Removing that marking requires a live run.

## Design

### 1. Extract `_CLIClient` in `analysis/llm.py`

A shared subprocess transport, mirroring how `_HTTPClient` backs Ollama,
OpenAI-compatible and Anthropic. The base owns the transport; subclasses own the
shape.

```python
class _CLIClient:
    BINARY = ""
    def _command(self, prompt, system) -> list[str]:  # full argv
    def _stdin(self, prompt) -> str | None            # what to pipe, if anything
    def _parse(self, stdout) -> str                   # default: stdout.strip()
    def generate(self, prompt, system=None) -> str
```

`generate` keeps the existing contract exactly: **never raises, returns `""` on
any failure**, so a broken backend falls through to no-trade rather than
aborting a scheduled run midway through a portfolio. It handles, in one place,
the five paths that must yield `""`:

- nonzero exit code (logging truncated stderr)
- `subprocess.TimeoutExpired`
- `FileNotFoundError` (binary not installed)
- unparseable output
- a well-formed payload with an error flag set

The binary is resolved as `config.LLM_CLI_BINARY or cls.BINARY`, and the
subprocess runs with `cwd` set to the isolation directory (§2).

`base_url`, `api_key` and `session` stay in the constructor signature, accepted
and ignored, so `build_client` can keep constructing every provider identically.

### 2. Per-provider shapes

| | binary | prompt | system prompt | parse |
|---|---|---|---|---|
| `claude-cli` | `claude` | stdin | `--append-system-prompt` | `is_error` / `result` |
| `cursor-cli` | `cursor-agent` | **argv** | prepended into prompt | `is_error` / `result` |
| `codex-cli` | `codex` | stdin (`-`) | prepended into prompt | plain stdout |

Cursor emits byte-identical JSON to Claude:

```json
{"type":"result","subtype":"success","is_error":false,
 "result":"<text>","session_id":"<uuid>"}
```

So both use a module-level `_json_result(stdout)` helper carrying the existing
`is_error`-before-`result` check. Codex needs no `_parse` override at all: it
streams progress to stderr and prints only the final agent message to stdout, so
the base implementation is already correct.

Commands:

```
claude       -p --output-format json --strict-mcp-config \
             --setting-sources "" --tools "" [--model M] \
             [--append-system-prompt S]                      # prompt on stdin

cursor-agent -p --output-format json --mode ask \
             [--model M] --workspace <ws> "<prompt>"

codex        exec --sandbox read-only --skip-git-repo-check \
             --cd <ws> [-m M] -                              # prompt on stdin
```

Two deliberate choices:

- **Cursor takes the prompt on argv**, diverging from the other two. Cursor's
  docs only ever show prompts as arguments and say nothing about stdin. macOS
  `ARG_MAX` is ~1MB, so a tens-of-KB analyst prompt fits comfortably. Following
  the only documented behaviour beats a uniform contract we cannot test.
- **`--mode ask` and `--sandbox read-only` are Cursor's and Codex's `--tools ""`.**
  The pipeline only generates text. `--force` / `--yolo` is never passed to
  Cursor, and Codex is never taken out of its read-only sandbox.

Neither new CLI documents a system-prompt flag, so `system` is prepended into
the prompt text as `f"{system}\n\n{prompt}"`.

### 3. Repo-context isolation

The reason `claude-cli` passes `--setting-sources ""` applies to both newcomers.
Codex reads `AGENTS.md`; Cursor reads `.cursor/rules`. A cron run launched from
this repo would drag trading-agent instructions into four text-generation calls
that need none of it — cost and latency, and a real risk of the CLI trying to
act on them.

Neither CLI has a flag that disables project context, so instead they are
pointed at an empty directory: a new `config.cli_workspace_dir()` under
`~/.stockbeat-agent/`, alongside the existing `agent_memory_dir()`. It is passed
via `--cd` / `--workspace` *and* as the subprocess `cwd`.

**Known limitation:** user-global config (`~/.codex/AGENTS.md`,
`~/.cursor/rules`) still applies. This is documented, not fixed.

### 4. `config.py`

- `_PROVIDER_DEFAULTS` gains `"cursor-cli": ("", "")` and
  `"codex-cli": ("", "gpt-5.1-codex-max")`.
- **Cursor's model default is deliberately empty**, so `build_client` refuses to
  start until the operator sets one. Cursor's model IDs vary by account and are
  not confidently known here; `LLM_MODEL` is also what gets stamped into
  StockBeat's `llm_model` field, so a wrong default would either fail mid-run or
  record a lie about which model made a trading decision. The existing
  "LLM_MODEL is required" error gains a hint pointing at
  `cursor-agent --list-models`.
- New `LLM_CLI_BINARY` env var overrides the binary name for any CLI provider.
  This is not polish: Cursor renamed its binary to `agent`, `cursor-agent` still
  works, and which one exists depends on how old the install is. It also accepts
  an absolute path.

### 5. `build_client`

`_NO_API_KEY_NEEDED` becomes a predicate rather than a tuple:

```python
issubclass(cls, _CLIClient) or cls is OllamaClient
```

Every CLI provider is exempt from the `LLM_API_KEY` check by construction, so
the next one added cannot forget to register itself. `build_client` otherwise
keeps its current behaviour of **raising on misconfiguration** rather than
returning a dud client — a run that cannot reach an LLM must stop before
fetching market data, or the pipeline reads an empty analyst report as a genuine
"no signal".

## Testing

**The existing 12 `claude_cli` tests must pass unchanged.** That is the proof
the refactor is behaviour-preserving, and it is the acceptance criterion for §1.
The only permitted edit to existing test code is adding a `cwd` kwarg to
`FakeRunner.__call__`.

New coverage, per new provider:

- argv shape: subcommand, print/JSON flags, model flag present when set and
  absent when not
- isolation flags present (`--mode ask`; `--sandbox read-only`,
  `--skip-git-repo-check`), and `--force` / `--yolo` never present
- prompt routing: Cursor's prompt is in argv and not stdin; Codex's is on stdin
  and not argv
- system prompt prepended into the prompt text
- binary override via `LLM_CLI_BINARY`
- the five failure paths from §1, each returning `""`
- Codex specifically: stdout is returned verbatim (modulo strip), and a
  non-JSON stdout is a success case, not a parse failure

Plus `build_client` construction for both providers, the no-API-key exemption,
and `config` provider-default coverage in `tests/test_config.py`.

## Documentation

**README** gains both rows in the provider table and a section per provider,
including the cost asymmetry stated plainly:

- ChatGPT Plus covers Codex CLI usage under the plan's rate limits.
- Cursor Pro includes a credit pool roughly equal to the subscription, and CLI
  runs draw it down like API usage. Four calls per agent per day can reach
  overages.

The existing `claude-cli` caveats extend to both: a real interactive login is
required, so this does not work inside the Docker image, and an unattended cron
run fails once the CLI's token expires.

**CLAUDE.md**'s "The `claude-cli` provider" section widens to cover all three,
recording the traps that look wrong to a passing reader:

- Never add `--bare` to the Claude command (restricts auth to
  `ANTHROPIC_API_KEY`, ignores the OAuth login).
- Check `is_error`, not `subtype` — true for Claude and Cursor alike.
- Codex's contract is stdout-only; adding `--json` would break `_parse` by
  turning stdout into a JSONL event stream.
- Cursor takes the prompt on argv, not stdin, unlike its two siblings.
- Both new providers are documented from vendor docs, not verified against a
  live binary.

## Out of scope

- Verifying either provider against a real binary (no subscription available).
- Gemini CLI, GitHub Copilot CLI, or any further provider.
- Retry, streaming, or per-provider timeout tuning. All CLI providers keep the
  existing 300s default.
- Any change to the pipeline, prompts, validator or execution layers.
