# Final Review Fixes

Applied from the whole-branch code review of `feat/cursor-codex-cli-providers`.

## IMPORTANT-1: `.env.example` — stale provider enumeration and misleading heading

**Changed:**
- Line 14: updated provider comment to include `cursor-cli | codex-cli`.
- Reworded the subscription block heading from "Claude Pro/Max subscription — no API key…" to "Subscription CLIs — no API key…", covering all three providers with their own login commands.

## IMPORTANT-2: `ARCHITECTURE.md` — "Adding an LLM provider" guidance was wrong

**Changed:**
- Replaced "Add a subclass of `_HTTPClient`" with a sentence distinguishing `_HTTPClient` (HTTP providers) from `_CLIClient` (CLI providers), and explaining that `_needs_api_key` exempts every `_CLIClient` subclass automatically.

## IMPORTANT-3: `analysis/llm.py` — `--mode ask` / `--sandbox read-only` comments overstated equivalence

**Changed:**
- `CursorCLIClient._command`: rewrote comment to say "closest available equivalent" and explain that unlike `--tools ""` these flags restrict writes rather than removing tool definitions, so the prompt reduction does not transfer.
- `CodexCLIClient._command`: same correction.
- `CLAUDE.md`: added sentence noting that `cursor-cli` and `codex-cli` use the closest available equivalents but the prompt reduction does not transfer.
- `CLAUDE.md` (workspace section): added known-limitation note that `--cd`/`--workspace` points at `~/.stockbeat-agent/cli-workspace`, whose parent contains each agent's `memory/decisions.jsonl`, which a read-only-sandboxed CLI can still read.
- `docs/superpowers/specs/2026-08-20-subscription-cli-providers-design.md`: corrected the `--mode ask` / `--sandbox read-only` equivalence claim.

## MINOR-4: Stale `llm.py` one-liners in `CLAUDE.md` and `README.md`

**Changed:**
- `CLAUDE.md` line 51: updated to "LLM clients (HTTP: Ollama/OpenAI-compatible/Anthropic; CLI: claude/cursor/codex)".
- `README.md` line 247: same update.

## MINOR-5: `_MODEL_HINTS` hardcoded binary name

**Changed:**
- `analysis/llm.py`: replaced hardcoded `"cursor-agent"` in the hint string with `config.LLM_CLI_BINARY or 'cursor-agent'`. The existing test `test_build_client_cursor_cli_requires_an_explicit_model` matches on `"--list-models"` substring — still passes.

## MINOR-6: `_json_result` lost provider identity in log line

**Changed:**
- `analysis/llm.py`: added `binary: str` parameter to `_json_result`, logs `"LLM | %s reported an error"` with the binary name.
- Updated both call sites: `ClaudeCLIClient._parse` and `CursorCLIClient._parse` pass `self._binary()`.

## MINOR-7a: Cursor argv — `--` terminator before prompt

**Changed:**
- `analysis/llm.py` `CursorCLIClient._command`: changed `cmd.append(text)` to `cmd += ["--", text]`.
- `tests/test_llm.py`: added `test_cursor_cli_double_dash_before_prompt` asserting `--` immediately precedes the prompt.
- Existing `test_cursor_cli_sends_prompt_on_argv_not_stdin` (`runner.cmd[-1] == "a very long prompt"`) still passes since `--` is second-to-last.

## MINOR-7b: ARG_MAX factually wrong for deployment target

**Changed:**
- `analysis/llm.py` `CursorCLIClient` docstring: corrected from "macOS ARG_MAX ~1MB" to Linux `MAX_ARG_STRLEN` = 128 KiB per single argument, real prompts 10-25 KB, margin ~5-10x.
- `tests/test_llm.py` `test_cursor_cli_sends_prompt_on_argv_not_stdin` docstring: same correction.
- `CLAUDE.md`: corrected same.
- `docs/superpowers/specs/2026-08-20-subscription-cli-providers-design.md`: corrected same.

## MINOR-10: `test_cli_workspace_dir_is_created_and_empty_of_agent_config` was near-tautological

**Changed:**
- `tests/test_config.py`: renamed to `test_cli_workspace_dir_is_created_and_not_the_repo`, kept `is_dir()` assertion, replaced `list(d.iterdir()) == []` (tautological with tmp_path) with an assertion that the path is not the current working directory — which is the property actually worth pinning.

## MINOR-11: `_needs_api_key` "by construction" property untested

**Changed:**
- `tests/test_llm.py`: added imports of `_CLIClient` and `_needs_api_key`.
- Added `test_needs_api_key_is_false_for_any_cli_client_subclass`: defines a throwaway `_CLIClient` subclass and asserts `_needs_api_key` returns False.
- Added `test_needs_api_key_is_true_for_non_cli_non_ollama_client`: asserts True for `AnthropicClient` and `OpenAICompatClient`.

## MINOR-12: Single blank line before `def _needs_api_key`

**Changed:**
- `analysis/llm.py`: added second blank line before `def _needs_api_key`.

## RECOMMENDATION: Timeout caveat for codex-cli

**Changed:**
- `README.md` caveats section: added one sentence noting all three CLI providers use 300s and that `codex exec` is an agentic loop, so the first real `codex-cli` user should verify whether 300s is sufficient.

## Test results

```
.venv/bin/pytest -q
369 passed in 0.74s
```

Previously 366; the 3 new tests are:
- `test_cursor_cli_double_dash_before_prompt`
- `test_needs_api_key_is_false_for_any_cli_client_subclass`
- `test_needs_api_key_is_true_for_non_cli_non_ollama_client`
