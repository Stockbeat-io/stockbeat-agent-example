import config
from analysis.prompts import build_analyst_prompt, build_judge_prompt


def test_persona_injected_into_analyst():
    config.AGENT_PERSONA = "You are Test Persona — a cautious investor." + " " * 50
    context = {"candidates": [{"ticker": "AAPL", "price": 200, "score": 5,
                "rsi": 55, "macd_hist": 0.1, "pe": 25, "revenue_growth": 0.1,
                "above_sma200": True}], "macro": {}}
    system, user = build_analyst_prompt(context)
    assert "Test Persona" in system


def test_persona_injected_into_judge():
    config.AGENT_PERSONA = "You are Test Persona — a cautious investor." + " " * 50
    context = {"candidates": [{"ticker": "AAPL", "price": 200, "score": 5,
                "rsi": 55, "macd_hist": 0.1, "pe": 25, "revenue_growth": 0.1,
                "above_sma200": True}],
               "macro": {}, "portfolio": {"total_equity": 100000,
               "available_cash": 100000, "trade_tokens": 20, "holdings": {}},
               "mode": "normal", "max_actions": 3}
    system, user = build_judge_prompt(context)
    assert "Test Persona" in system


def test_no_persona_uses_default():
    config.AGENT_PERSONA = ""
    context = {"candidates": [], "macro": {}}
    system, user = build_analyst_prompt(context)
    assert "financial analyst" in system.lower()
