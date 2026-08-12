# StockBeat Autonomous Trading Agent

## What This Is

An autonomous trading agent that runs daily on Ollama (mistral:7b), analyzes S&P 500 stocks using free data sources, and executes trades on the StockBeat virtual trading arena via its REST API. Goal: maximize BeatScore (alpha vs SPY).

## Design Spec

Read the full design before making changes: `docs/superpowers/specs/2026-06-27-stockbeat-autonomous-agent-design.md`

## Related Projects (READ THESE)

- **`~/stockbeat/`** — The StockBeat platform itself. Study `backend/src/routes/trades.js` and `backend/src/services/tradeService.js` for trade API behavior, validation rules, and error codes. `backend/src/config/arenaConfig.js` has S&P 500 whitelist, trade limits, and BeatScore parameters.
- **`~/TradingAgents/`** — Multi-agent LLM trading framework. Borrow patterns from:
  - `tradingagents/dataflows/` — Data fetching (yfinance, StockTwits, Reddit, FRED)
  - `tradingagents/agents/utils/memory.py` — Decision logging and reflection
  - `tradingagents/agents/analysts/` — Prompt structure for financial analysis
  - `tradingagents/llm_clients/openai_client.py` — Ollama integration via OpenAI-compatible API
  - Do NOT import TradingAgents as a dependency. Rewrite what you need, lightweight.

## Architecture

```
main.py (orchestrator)
  ├── 1. Portfolio State      ← Stockbeat API (GET /api/v1/portfolio, /orders/pending, /universe)
  ├── 2. Screener             ← yfinance batch, no LLM, pure math (RSI, MACD, SMA scoring)
  ├── 3. Data Enrichment      ← yfinance + StockTwits + Reddit + FRED, no LLM
  ├── 4. LLM Pipeline         ← Ollama mistral:7b, 4 sequential calls:
  │     ├── Analyst (structured report across technical/fundamental/sentiment/macro)
  │     ├── Bull (buy recommendations with evidence)
  │     ├── Bear (counterarguments + sell recommendations)
  │     └── Judge (final JSON actions, optimizing for BeatScore)
  ├── 5. Validation + Execution → hard-coded risk rules + MCP tools (buy, sell, etc.)
  ├── 6. Comment Replies       → MCP: list_comments → LLM triage → reply_to_comment
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
data/
  market.py            — yfinance batch OHLCV + technical indicators
  fundamentals.py      — P/E, revenue growth
  sentiment.py         — StockTwits + Reddit
  news.py              — Yahoo Finance headlines
  macro.py             — FRED macro data
  screener.py          — S&P 500 → top 15 candidates (no LLM)
analysis/
  prompts.py           — prompt templates for all 4 LLM calls
  llm.py               — Ollama HTTP client
  pipeline.py          — Analyst → Bull → Bear → Judge orchestration
execution/
  mcp_client.py        — MCP-based Stockbeat client (auto-discovers tools via /mcp)
  stockbeat_client.py  — Stockbeat REST API wrapper (fallback)
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

### Statistical honesty
Per-decision alpha has a standard deviation of ~6pp, so detecting a 2pp edge at
95% confidence needs ~37 decisions **per agent**. Below `review.MIN_SAMPLES`
(30), `memory/review.py` labels an agent's ranking as insufficient. A five-week
sample produced a best-worst agent spread with p=0.059 against a permutation
null — indistinguishable from chance. Do not build agent-ranking logic on small
samples; prefer pooled cross-agent feature analysis, where n is large enough.

### Known gap
Decisions logged before 2026-08-08 have `features: null` — the screening
evidence behind them was never recorded, so attribution only covers decisions
made after that date. `legacy_outcome` holds the old (unreliable) 1-day result.

## Key Constraints

### StockBeat Trade API (POST /api/v1/trades)
- `action`: BUY | SELL | CLOSE_STOCK | CLOSE_ALL | BUY_LIMIT | STOP_LOSS | CANCEL_ORDER
- `why`: 200-400 characters, required for BUY/SELL/CLOSE actions
- `target_price`: required for BUY and BUY_LIMIT. Must be > entry price, ≤ 2.5x entry price
- `target_horizon_days`: required for BUY and BUY_LIMIT. Positive integer
- `usd_amount`: minimum $1,000
- Auth: `X-API-Key: sk_live_...` header
- Trades during market hours execute immediately; after hours → PENDING → MOO next day

### Risk Rules (hard-coded in validator.py, NOT LLM-dependent)
- Max 15% of total_equity in one position
- Keep 20-40% cash
- 5-8 positions for diversification
- Max 3 trades per normal run (initial build: 8-10)
- Stop-loss at 5-7% below entry
- A discretionary SELL is blocked inside the agent's `min_holding_days`.
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
- Ollama with mistral:7b (localhost:11434)
- Dependencies: yfinance, requests, fredapi
- No LangGraph, no langchain, no heavy frameworks
- macOS launchd for daily scheduling (22:00 Israel time / 15:00 ET)

## Environment Variables (.env)

```
STOCKBEAT_API_KEY=sk_live_...
STOCKBEAT_BASE_URL=https://stockbeat.app
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=mistral:7b
FRED_API_KEY=...
```

## Conventions

- Keep it lightweight — this entire project should be ~600-800 lines of Python
- All market data comes from code, never from LLM generation (LLM interprets, doesn't invent)
- Safe default: when in doubt, do nothing (skip trade, exit early)
- Log everything to `logs/YYYY-MM-DD.log`
