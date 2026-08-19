# StockBeat Autonomous Trading Agent

## What This Is

An autonomous trading agent that runs daily, analyzes S&P 500 stocks using free data sources, and executes trades on the StockBeat virtual trading arena via its REST API. Goal: maximize BeatScore (alpha vs SPY).

## Design Spec

Read the full design before making changes: `ARCHITECTURE.md`

## Architecture

```
main.py (orchestrator)
  ├── 1. Portfolio State      ← StockBeat API (GET /api/v1/portfolio, /orders/pending, /universe)
  ├── 2. Screener             ← yfinance batch, no LLM, pure math (RSI, MACD, SMA scoring)
  ├── 3. Data Enrichment      ← yfinance + StockTwits + Reddit + FRED, no LLM
  ├── 4. LLM Pipeline         ← 4 sequential calls:
  │     ├── Analyst (structured report across technical/fundamental/sentiment/macro)
  │     ├── Bull (buy recommendations with evidence)
  │     ├── Bear (counterarguments + sell recommendations)
  │     └── Judge (final JSON actions, optimizing for BeatScore)
  ├── 5. Validation + Execution → hard-coded risk rules + StockBeat client (buy, sell, etc.)
  ├── 6. Comment Replies       → list_comments → LLM triage → reply_to_comment
  └── 7. Memory logging        → ~/.stockbeat-agent/<agent>/memory/decisions.jsonl
```

Two steps run before the LLM pipeline and do not depend on it:

- **Stop management** (`execution/stops.py`) re-evaluates every holding each run.
  Stop-loss updates consume no trade tokens, so this is free and unconditional.
- **Grading** (`memory/memory.py`) attaches alpha checkpoints to past decisions
  as their windows complete.

## Project Structure

```
config.py              — env vars, risk rules, constants
main.py                — orchestrator (runs the pipeline)
profiles/              — agent persona JSON files (one per agent, no API keys)
data/
  market.py            — yfinance batch OHLCV + technical indicators
  fundamentals.py      — P/E, revenue growth
  sentiment.py         — StockTwits + Reddit
  news.py              — Yahoo Finance headlines
  macro.py             — FRED macro data
  screener.py          — S&P 500 → top 15 candidates (no LLM)
  enrichment.py        — assembles per-candidate data dicts
analysis/
  prompts.py           — prompt templates for all 4 LLM calls
  llm.py               — LLM client (Ollama, OpenAI-compatible, Anthropic)
  pipeline.py          — Analyst → Bull → Bear → Judge orchestration
execution/
  mcp_client.py        — MCP-based StockBeat client (auto-discovers tools via /mcp)
  stockbeat_client.py  — StockBeat REST API wrapper (fallback)
  validator.py         — hard-coded pre-trade risk rules
  stops.py             — trailing stop-loss ratchet (no LLM, no token cost)
memory/
  memory.py            — decision log, multi-checkpoint grading, lessons
  review.py            — cross-agent performance report (python -m memory.review)
scripts/
  backfill.py          — one-off: grade historical decisions from price history
```

## Learning Loop

### Grading
A decision stays `open` and accumulates alpha checkpoints at 5d/20d/60d and at
its own `target_horizon_days`, becoming `graded` only when that horizon lands.
Checkpoints are **write-once**. `excursion` tracks max favourable/adverse move
while open — that is what shows whether a trailing stop captured a gain.

`target_horizon_days` is calendar days; checkpoints are trading sessions. The
conversion lives in `memory.memory._trading_days`.

## Key Constraints

### StockBeat Trade API (POST /api/v1/trades)
- `action`: BUY | SELL | CLOSE_STOCK | CLOSE_ALL | BUY_LIMIT | STOP_LOSS | CANCEL_ORDER
- `why`: 200-400 characters, required for BUY/SELL/CLOSE actions
- `target_price`: required for BUY and BUY_LIMIT. Must be > entry price, ≤ 2.5x entry price
- `target_horizon_days`: required for BUY and BUY_LIMIT. Positive integer
- `usd_amount`: minimum $1,000
- `llm_model`: required for BUY/SELL/CLOSE_STOCK/BUY_LIMIT and new STOP_LOSS
  placements. 2-30 chars, `[A-Za-z0-9 ._:/+()-]`. Optional on a stop-loss
  *update*, absent on CANCEL_ORDER
- Auth: `X-API-Key: sk_live_...` header
- Trades during market hours execute immediately; after hours → PENDING → MOO next day

**Required fields are the client's job, not the LLM's.** Both clients stamp
`llm_model` from `config.LLM_MODEL` inside `submit_trade`, driven by
`config.ACTIONS_REQUIRING_LLM_MODEL`, so no call site can omit it and the value
follows whichever provider is configured. When the platform adds a required
field, add it there — a Python client never self-corrects the way a prompt does.

`mcp_client._call_tool` must stay tolerant of both: the MCP SDK names the error
flag `is_error` in Python and `isError` only on the wire, and a schema rejection
arrives as prose, not JSON. Reading only the wire name and calling `json.loads`
on prose turns every rejection into a crash that kills the run before it writes
its report or logs its decisions.

### Risk Rules (hard-coded in validator.py, NOT LLM-dependent)
- Position size clamped to 5-20% of total equity
- Cash target range 10-50% of equity (default 30%)
- Max 3 trades per normal run (initial build: up to 10)
- Stop-loss range 3-15% below entry (default 6%)
- A discretionary SELL is blocked inside the agent's `min_holding_days` (default 3).
  STOP_LOSS is exempt, so a genuine break can always be cut.

**Cash accounting:** `validate_buy` checks the reserve against the
`available_cash` it is handed, so a caller validating several buys in one run
MUST decrement it between calls (`main.run` tracks `committed_cash`). Passing
the same run-start snapshot to every buy is how a 10-action initial build once
spent 100% of equity against a 20% cash target.

**Rationale echo:** `validator.echoes_example()` drops any trade whose `why`
reuses `prompts.GOOD_WHY_EXAMPLE`. Small models copy few-shot examples verbatim;
that text reached 30 live rationales, including cloud-revenue reasoning for a
chemicals company. If you edit the example, the guard follows it automatically.

### Initial Portfolio Build
- Triggered when: 100% cash AND trade_tokens ≥ 15
- Build 6-10 positions across 4+ sectors
- After day 1, switch to normal mode (0-3 trades/day)

## Tech Stack

- Python 3.10+
- LLM: Ollama is the default (local, free). OpenAI-compatible and Anthropic backends are also supported via `LLM_PROVIDER`.
- Dependencies: yfinance, requests, fredapi
- No LangGraph, no langchain, no heavy frameworks

## Environment Variables (.env)

```
STOCKBEAT_API_KEY_TECHNICAL_EXAMPLE=sk_live_...
STOCKBEAT_BASE_URL=https://stockbeat.io
LLM_PROVIDER=ollama
LLM_BASE_URL=http://localhost:11434
LLM_MODEL=mistral:7b
LLM_API_KEY=
FRED_API_KEY=
```

Set one `STOCKBEAT_API_KEY_<PROFILE_NAME_UPPER>` per profile. The `LLM_API_KEY`
is not needed when `LLM_PROVIDER=ollama`.

## Conventions

- Keep it lightweight — this entire project should be ~600-800 lines of Python
- All market data comes from code, never from LLM generation (LLM interprets, doesn't invent)
- Safe default: when in doubt, do nothing (skip trade, exit early)
- Log everything to `logs/<agent-name>/YYYY-MM-DD.log`
