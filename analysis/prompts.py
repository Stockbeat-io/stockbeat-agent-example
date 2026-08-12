import config

# Illustration of the depth a `why` should have. Small models copy few-shot
# examples verbatim — this text appeared unchanged in 30 live trade rationales
# across 14 agents, including "cloud revenue" as the stated reason for buying a
# chemicals company. It is a module constant so execution.validator can reject
# any `why` that echoes it, and so that guard tracks edits to the example.
GOOD_WHY_EXAMPLE = (
    "Cloud revenue acceleration exceeded street estimates two quarters running, "
    "while the AI infrastructure buildout provides a multi-year growth runway. "
    "Margin expansion from mix shift toward higher-margin services offsets "
    "hardware cyclicality risk flagged by the bear."
)


def _with_persona(base_system: str) -> str:
    """Prepend agent persona to system prompt if configured, otherwise return unchanged."""
    if config.AGENT_PERSONA:
        return config.AGENT_PERSONA + "\n\n" + base_system
    return base_system


_SYSTEM = (
    "You are a disciplined portfolio manager for a virtual S&P 500 trading arena. "
    "Your objective is to maximize BeatScore: consistent daily alpha versus SPY "
    "while staying fully invested and diversified. Prefer steady small outperformance "
    "over volatile high-conviction bets. You never invent data; you only reason over "
    "the data provided. Respond with ONLY valid JSON, no prose."
)


def _fmt_candidate(c: dict) -> str:
    return (
        f"- {c['ticker']}: price=${c.get('price')}, score={c.get('score')}, "
        f"rsi={c.get('rsi')}, macd_hist={c.get('macd_hist')}, "
        f"pe={c.get('pe')}, rev_growth={c.get('revenue_growth')}, "
        f"above_sma200={c.get('above_sma200')}"
    )


_ANALYST_SYSTEM = (
    "You are a financial analyst. Produce a concise, structured report. "
    "You never invent data; reason only over the data provided."
)


def build_analyst_prompt(context: dict) -> tuple:
    candidates = "\n".join(_fmt_candidate(c) for c in context["candidates"])
    macro = context.get("macro") or {}
    macro_line = ", ".join(f"{k}={v}" for k, v in macro.items()) or "n/a"
    user = f"""Analyze these S&P 500 candidates. Macro environment: {macro_line}

CANDIDATES:
{candidates}

For each ticker, give a one-line verdict across four domains — Technical, Fundamental,
Sentiment, Macro relevance — then a signal label (BULLISH/NEUTRAL/BEARISH) and a
confidence 1-5. Keep the whole report under 800 words."""
    return _with_persona(_ANALYST_SYSTEM), user


_BULL_SYSTEM = (
    "You are a bullish investment advocate. Argue for positions that will beat SPY. "
    "Cite specific data points from the analyst report. No invented data."
)


def build_bull_prompt(context: dict, analyst_report: str) -> tuple:
    user = f"""ANALYST REPORT:
{analyst_report}

Recommend up to {context['max_actions']} BUY ideas most likely to generate alpha vs SPY.
For each: ticker, why (citing the report), a target_price, target_horizon_days, and the
expected edge over SPY. Prefer steady outperformance over volatile bets."""
    return _with_persona(_BULL_SYSTEM), user


_BEAR_SYSTEM = (
    "You are a bearish risk analyst. Challenge the bull with concrete data-backed risks "
    "and identify deteriorating holdings to SELL. No vague warnings; cite specifics."
)


def build_bear_prompt(context: dict, analyst_report: str, bull_case: str) -> tuple:
    holdings = list(context["portfolio"].get("holdings", {}).keys())
    user = f"""ANALYST REPORT:
{analyst_report}

BULL CASE:
{bull_case}

Current holdings: {holdings or 'none'}.
Counter each bull recommendation with specific risks from the data. Then recommend any
holdings to SELL due to deteriorating signals, with concrete reasons."""
    return _with_persona(_BEAR_SYSTEM), user


def build_judge_prompt(context: dict) -> tuple:
    p = context["portfolio"]
    mode = context.get("mode", "normal")
    max_actions = context["max_actions"]

    if mode == "initial":
        objective = (
            f"INITIAL BUILD: You are 100% cash. You MUST buy 6-10 different stocks "
            f"across 4+ sectors to build a diversified portfolio. Return at least 6 "
            f"BUY actions (max {max_actions}). Do NOT return only 1 action."
        )
    else:
        objective = (
            f"NORMAL RUN: pick 0 to {max_actions} of the best actions. "
            f"Doing nothing (empty array) is allowed and often correct."
        )

    candidates = "\n".join(_fmt_candidate(c) for c in context["candidates"])

    debate = ""
    if context.get("analyst"):
        debate += f"\nANALYST REPORT:\n{context['analyst']}\n"
    if context.get("bull"):
        debate += f"\nBULL CASE:\n{context['bull']}\n"
    if context.get("bear"):
        debate += f"\nBEAR CASE:\n{context['bear']}\n"
    if context.get("lessons"):
        debate += f"\nPAST LESSONS (memory):\n{context['lessons']}\n"

    user = f"""{objective}

PORTFOLIO:
- total_equity: ${p.get('total_equity')}
- available_cash: ${p.get('available_cash')}
- trade_tokens: {p.get('trade_tokens')}
- holdings: {list(p.get('holdings', {}).keys()) or 'none (100% cash)'}
{debate}
RISK GUIDELINES (you decide within these bounds):
- Assess the macro environment, sentiment, and technicals to set your risk stance.
- cash_target_pct: how much cash to hold. Must match your stance:
  aggressive=10-20%, neutral=20-35%, defensive=35-50%.
  Being under-invested hurts BeatScore (exposure penalty), so don't hoard cash without reason.
- default_position_pct: max % of equity per new position (5-20%).
  Higher conviction + lower volatility -> larger position. Spread risk across stocks.
- stop_loss_price: per trade, based on support levels, volatility, and conviction.
  Must be 3-15% below current price. Tight for high-conviction; wide for volatile names.
- position_pct (optional): override default_position_pct for a specific trade (5-20%).

NON-NEGOTIABLE (API constraints):
- Minimum trade size ${config.MIN_TRADE_USD}.
- target_price must be > entry price and <= {config.TARGET_MAX_UPSIDE}x entry price.
- target_horizon_days: integer {config.HORIZON_MIN}-{config.HORIZON_MAX}.
- "why" text must be 200-350 characters (hard limit, will be cut at 400).
- Use at most {max_actions} actions.

CANDIDATES:
{candidates}

WRITING "why" (MUST be 200-400 chars — this is shown to viewers):
- Do NOT repeat the ticker name, price, RSI, PE, or any data already in the action fields.
- Focus on the REASONING: why this stock will outperform SPY, what catalyst or edge exists,
  what makes the timing right. Each "why" must be unique — no copy-paste with swapped tickers.
- Reference specifics from the debate: analyst findings, bull/bear arguments, sector dynamics.
- Write at least 3 complete sentences. Generic filler like "strong fundamentals" is not enough.
- BAD example: "AAPL has a strong brand, making it attractive." (too short, too generic)
- GOOD example (ILLUSTRATION ONLY — about a different company, on a different day):
  "{GOOD_WHY_EXAMPLE}"
- The example above shows the required DEPTH, not words to reuse. It describes a
  cloud/AI business; do not apply its reasoning to an unrelated company. Any "why"
  that reuses its phrasing is REJECTED and the trade is dropped. Write your own
  reasoning from this run's actual debate and data.

Respond with ONLY a JSON object in this exact schema:
{{
  "risk_assessment": {{
    "stance": "aggressive|neutral|defensive",
    "cash_target_pct": <10-50>,
    "default_position_pct": <5-20>,
    "reasoning": "1-2 sentences explaining your risk read of the current environment"
  }},
  "actions": [
    {{"action": "BUY", "ticker": "<TICKER>", "usd_amount": <dollars>,
      "target_price": <price above current>,
      "target_horizon_days": <{config.HORIZON_MIN}-{config.HORIZON_MAX}>,
      "why": "<200-400 chars: unique reasoning about edge vs SPY>",
      "stop_loss_price": <3-15% below current price>,
      "position_pct": <5-20>}}
  ]
}}
{"IMPORTANT: " + objective}
Allowed actions: BUY, SELL. Return empty actions array if no action beats doing nothing."""

    return _with_persona(_SYSTEM), user


_COMMENT_REPLY_SYSTEM = (
    "You are responding to human comments on your trades. Stay in character. "
    "Defend your thesis with specifics when challenged. Revise honestly if "
    "the commenter raises a valid point. Be concise (max 300 chars per reply). "
    "Never hand-wave. You must reply to at least one comment."
)


def _fmt_comment(c: dict) -> str:
    return (
        f"- comment_id: \"{c['comment_id']}\"\n"
        f"  on_trade: {c['trade_action']} {c['trade_ticker']}\n"
        f"  your_why: \"{c['trade_why']}\"\n"
        f"  comment: \"{c['body']}\"\n"
        f"  comments_count: {c['comments_count']}\n"
        f"  posted: \"{c['age_label']}\""
    )


def build_comment_reply_prompt(context: dict) -> tuple:
    comments = "\n\n".join(_fmt_comment(c) for c in context["comments"])
    user = f"""COMMENTS TO REVIEW:

{comments}

Pick which comments to reply to (at least 1). Prioritize:
- Comments on trades with high comments_count (more people see your reply)
- Direct questions and thesis challenges
- Recent comments

Respond with ONLY a JSON array:
[{{"comment_id": "abc123", "reply": "your reply here (1-300 chars)"}}]"""
    return _with_persona(_COMMENT_REPLY_SYSTEM), user
