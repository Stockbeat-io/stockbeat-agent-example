from analysis.prompts import build_judge_prompt

CONTEXT = {
    "mode": "normal",
    "max_actions": 3,
    "portfolio": {"total_equity": 100_000, "available_cash": 100_000,
                  "trade_tokens": 4, "holdings": {}},
    "candidates": [
        {"ticker": "AAPL", "price": 200.0, "score": 5, "rsi": 28.0,
         "macd_hist": 0.4, "pe": 30.0, "revenue_growth": 0.08,
         "above_sma200": True},
    ],
}


def test_returns_system_and_user():
    system, user = build_judge_prompt(CONTEXT)
    assert isinstance(system, str) and isinstance(user, str)
    assert system  # non-empty


def test_user_prompt_contains_rules_and_candidate():
    _, user = build_judge_prompt(CONTEXT)
    assert "AAPL" in user
    assert "200-400" in user            # why length rule
    assert "max 3" in user.lower() or "at most 3" in user.lower()
    assert "json" in user.lower()
    assert "actions" in user            # actions array in schema


def test_initial_mode_changes_instruction():
    ctx = dict(CONTEXT, mode="initial", max_actions=10)
    _, user = build_judge_prompt(ctx)
    assert "diversif" in user.lower()


def test_analyst_prompt_includes_data_and_macro():
    from analysis.prompts import build_analyst_prompt
    ctx = dict(CONTEXT, macro={"fed_funds": 5.0, "cpi_yoy": 3.1})
    system, user = build_analyst_prompt(ctx)
    assert "AAPL" in user
    assert "technical" in user.lower() and "fundamental" in user.lower()
    assert "5.0" in user  # macro surfaced


def test_bull_prompt_uses_report_and_max_actions():
    from analysis.prompts import build_bull_prompt
    _, user = build_bull_prompt(CONTEXT, "Analyst says AAPL strong.")
    assert "Analyst says AAPL strong." in user
    assert "alpha" in user.lower()


def test_bear_prompt_references_bull_and_holdings():
    from analysis.prompts import build_bear_prompt
    _, user = build_bear_prompt(CONTEXT, "report", "BUY AAPL")
    assert "BUY AAPL" in user
    assert "sell" in user.lower()


def test_judge_prompt_includes_debate_when_present():
    _, user = build_judge_prompt(dict(
        CONTEXT, analyst="ANALYSIS_X", bull="BULL_X", bear="BEAR_X", lessons="LESSON_X"))
    assert "ANALYSIS_X" in user and "BULL_X" in user
    assert "BEAR_X" in user and "LESSON_X" in user


def test_judge_prompt_contains_risk_guidelines():
    system, user = build_judge_prompt(CONTEXT)
    assert "cash_target_pct" in user
    assert "default_position_pct" in user
    assert "stop_loss_price" in user
    assert "10-50" in user or "10-50%" in user
    assert "5-20" in user or "5-20%" in user


def test_judge_prompt_contains_risk_assessment_schema():
    system, user = build_judge_prompt(CONTEXT)
    assert "risk_assessment" in user
    assert "stance" in user


def test_judge_prompt_no_hard_coded_cash_pct():
    system, user = build_judge_prompt(CONTEXT)
    assert "Keep 20-40% cash" not in user
    assert "Max 15% of equity" not in user


# --- Comment reply prompt ---

from analysis.prompts import build_comment_reply_prompt


def test_comment_reply_prompt_contains_comment_text():
    ctx = {
        "comments": [
            {"comment_id": "c1", "body": "Why did you buy this?",
             "trade_ticker": "AAPL", "trade_action": "BUY",
             "trade_why": "Strong momentum signals.", "comments_count": 5,
             "age_label": "2h ago"},
        ]
    }
    system, user = build_comment_reply_prompt(ctx)
    assert "Why did you buy this?" in user
    assert "AAPL" in user
    assert "comment_id" in user
    assert "at least 1" in user.lower() or "at least one" in user.lower()
    assert "300" in user or "300" in system


def test_comment_reply_prompt_persona_injected():
    import config
    old = config.AGENT_PERSONA
    config.AGENT_PERSONA = "You are Momentum Mike."
    try:
        ctx = {"comments": [{"comment_id": "c1", "body": "Q",
               "trade_ticker": "X", "trade_action": "BUY",
               "trade_why": "W", "comments_count": 1, "age_label": "1h ago"}]}
        system, _ = build_comment_reply_prompt(ctx)
        assert "Momentum Mike" in system
    finally:
        config.AGENT_PERSONA = old
