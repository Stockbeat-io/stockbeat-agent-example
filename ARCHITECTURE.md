# Architecture

This document describes how the agent is structured, why certain design decisions were made, and where to add new capabilities.

---

## Pipeline stages

The orchestrator (`main.py`) runs seven stages in order:

| # | Stage | Module | Inputs | Outputs |
|---|---|---|---|---|
| 1 | Portfolio state | `execution/stockbeat_client.py` or `execution/mcp_client.py` | StockBeat API | holdings, pending orders, trade tokens, universe |
| 2 | Screener | `data/screener.py` + `data/market.py` | S&P 500 ticker list, yfinance OHLCV | scored shortlist of candidates |
| 3 | Data enrichment | `data/enrichment.py`, `data/fundamentals.py`, `data/sentiment.py`, `data/news.py`, `data/macro.py` | candidate tickers | per-candidate dicts with technical, fundamental, sentiment, news, macro fields |
| 4 | LLM pipeline | `analysis/pipeline.py` | candidate dicts, portfolio state, recent lessons | risk assessment, list of proposed actions, debate transcript |
| 5 | Validation and execution | `execution/validator.py`, StockBeat client | proposed actions, portfolio snapshot, universe | executed trades |
| 6 | Comment replies | `analysis/pipeline.py` | recent comments | submitted reply texts |
| 7 | Memory logging | `memory/memory.py` | executed trades, candidate features | appended records in `decisions.jsonl` |

---

## Why stop management and grading run first

Two operations run before the LLM pipeline and before the trade-token check:

- **Stop management** (`execution/stops.py`) re-evaluates every open position each run. It places missing stop-losses and ratchets existing ones upward when the position has gained. Stop-loss updates cost no trade tokens, so protecting open positions is free and must never be gated behind a budget the agent has already spent. An agent that has exhausted its tokens still needs its winning positions protected — that is precisely when the ratchet matters most.

- **Grading** (`memory/memory.py`) attaches alpha checkpoints to past decisions as their windows complete. Grading is a local read/write operation against `decisions.jsonl` and does not touch the StockBeat API. It runs unconditionally so that the learning loop keeps advancing even on days when the agent cannot trade.

---

## The validator

Every proposed action passes through `execution/validator.py` before it is submitted. The validator enforces hard-coded rules independently of the LLM; it cannot be overridden by the model's output.

| Rule | Failure it prevents |
|---|---|
| Ticker in universe | Buying a non-S&P-500 stock |
| Position size clamped to 5–20% of equity | Micro-sizing or over-concentrating |
| Remaining cash after trade >= cash target | Draining the reserve |
| Trade size >= $1,000 | Submitting below the StockBeat minimum |
| Target price >= 5% above entry and <= 2.5x entry | Invalid or trivial targets |
| Rationale length 200–400 characters | Empty or runaway `why` fields |
| Holding period check for discretionary SELLs | Churning positions before the thesis can play out |
| Rationale echo check | Copied few-shot example reaching live trades |

### Cash accounting

`validate_buy` checks the cash reserve against the `available_cash` value it is handed, not against the portfolio snapshot fetched at the start of the run. When the orchestrator validates several buys in one run it must decrement `available_cash` between calls — `main.run` tracks this with `committed_cash`. Passing the same run-start snapshot to every buy caused a 10-action initial build to spend 100% of equity against a 20% cash target, leaving the agent unable to trade at all the next day.

### Rationale echo

`validator.echoes_example()` drops any trade whose `why` field reuses the illustrative example from `analysis/prompts.py` (`GOOD_WHY_EXAMPLE`). Small models copy few-shot examples verbatim; the example text appeared in 30 live rationales before the guard existed, including cloud-revenue reasoning applied to a chemicals company. If you edit the example in `prompts.py`, the guard follows it automatically because it matches against the current value of that constant, not a hard-coded string.

---

## The learning loop

Each executed trade is logged to `~/.stockbeat-agent/<agent-name>/memory/decisions.jsonl`.

A decision starts `open` and accumulates alpha checkpoints at 5 trading days, 20 trading days, 60 trading days, and at its own `target_horizon_days`. It becomes `graded` only when the horizon window closes. Checkpoints are write-once — once a checkpoint is recorded it is never overwritten.

`excursion` tracks the maximum favourable and adverse price movement while the decision is open. This reveals whether a trailing stop captured a gain (the stop was raised into a move that later reversed) or whether the position was closed at a loss.

`target_horizon_days` is stored in calendar days. Checkpoints are measured in trading sessions. The conversion between the two is handled in `memory.memory._trading_days`, which counts market-open days using the NYSE calendar.

---

## Extension points

### Adding a data source

Write a module in `data/` that exposes a plain function returning a dict or list of dicts. Call it from `data/enrichment.py`. The result flows into the analyst prompt automatically.

There is deliberately no provider protocol in `data/`. LLM backends are interchangeable — they accept the same prompt and return the same string — so a `LLMClient` protocol makes sense (`analysis/llm.py` defines one). Data sources return fundamentally different shapes: OHLCV arrays, sentiment counts, macro time series. A shared interface would be fake symmetry that adds no safety and forces awkward adapters. Write the simplest function that returns what the prompt needs.

### Adding an LLM provider

Add a subclass of `_HTTPClient` in `analysis/llm.py` and register it in `_PROVIDERS`. The only interface requirement is the `generate(prompt, system)` method defined by the `LLMClient` protocol.

`generate` must never raise. Transport or parse failures should log and return an empty string, allowing the pipeline to fall through to a no-trade outcome rather than aborting a scheduled run midway through a portfolio.

### Adding a risk rule

Add a check in `execution/validator.py`. `validate_buy` and `validate_sell` return `None` to drop an action or a modified dict to allow it through. Rules that compute a clamped value (position size, target price) should update the action dict rather than blocking it outright — this keeps the LLM's intent intact while correcting out-of-bounds numbers.

### Adding an agent persona

Copy a JSON file from `profiles/`, rename it so the filename matches the `name` field, set `persona_type` to one of the five values (`Technical`, `Fundamental`, `Sentiment`, `Macro`, `Hybrid`), and rewrite `persona`. The `persona` string is injected into all four LLM prompts and is the primary lever on agent behaviour. Set `risk_overrides` to tune the default position size, cash target, stop depth, or minimum holding days for that persona.
