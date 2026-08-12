from analysis.pipeline import run_judge

CONTEXT = {
    "mode": "normal", "max_actions": 3,
    "portfolio": {"total_equity": 100_000, "available_cash": 100_000,
                  "trade_tokens": 4, "holdings": {}},
    "candidates": [{"ticker": "AAPL", "price": 200.0, "score": 5, "rsi": 28.0,
                    "macd_hist": 0.4, "pe": 30.0, "revenue_growth": 0.08,
                    "above_sma200": True}],
}


class FakeLLM:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = 0

    def generate(self, prompt, system=None):
        self.calls += 1
        return self.responses[min(self.calls - 1, len(self.responses) - 1)]


def test_run_judge_parses_first_try():
    llm = FakeLLM('[{"action": "BUY", "ticker": "AAPL", "usd_amount": 5000}]')
    risk, actions = run_judge(llm, CONTEXT)
    assert actions[0]["ticker"] == "AAPL"
    assert llm.calls == 1


def test_run_judge_retries_once_then_succeeds():
    llm = FakeLLM("garbage no json",
                  '[{"action": "BUY", "ticker": "MSFT", "usd_amount": 3000}]')
    risk, actions = run_judge(llm, CONTEXT)
    assert actions[0]["ticker"] == "MSFT"
    assert llm.calls == 2


def test_run_judge_returns_empty_after_two_failures():
    llm = FakeLLM("garbage", "still garbage")
    risk, actions = run_judge(llm, CONTEXT)
    assert actions == []
    assert risk is None
    assert llm.calls == 2


def test_run_judge_accepts_empty_array():
    llm = FakeLLM("[]")
    risk, actions = run_judge(llm, CONTEXT)
    assert actions == []
    assert llm.calls == 1


def test_run_judge_returns_risk_assessment():
    llm = FakeLLM('{"risk_assessment": {"stance": "neutral", "cash_target_pct": 25, "default_position_pct": 12, "reasoning": "stable"}, "actions": [{"action": "BUY", "ticker": "AAPL", "usd_amount": 5000}]}')
    risk, actions = run_judge(llm, CONTEXT)
    assert risk["stance"] == "neutral"
    assert risk["cash_target_pct"] == 25
    assert actions[0]["ticker"] == "AAPL"


def test_run_judge_plain_array_returns_none_risk():
    llm = FakeLLM('[{"action": "BUY", "ticker": "AAPL", "usd_amount": 5000}]')
    risk, actions = run_judge(llm, CONTEXT)
    assert risk is None
    assert actions[0]["ticker"] == "AAPL"


def test_run_analyst_returns_report():
    from analysis.pipeline import run_analyst
    llm = FakeLLM("Technical: bullish. Fundamental: fair.")
    ctx = dict(CONTEXT, macro={})
    assert "bullish" in run_analyst(llm, ctx).lower()


def test_run_bull_returns_text():
    from analysis.pipeline import run_bull
    llm = FakeLLM("BUY AAPL: momentum + cheap forward P/E.")
    assert "AAPL" in run_bull(llm, CONTEXT, "report")


def test_run_bear_returns_text():
    from analysis.pipeline import run_bear
    llm = FakeLLM("Risk: AAPL P/E elevated. Consider trimming.")
    assert "Risk" in run_bear(llm, CONTEXT, "report", "bull")


def test_run_debate_runs_four_calls_and_parses():
    from analysis.pipeline import run_debate
    llm = FakeLLM("analyst report", "bull case", "bear case",
                  '[{"action": "BUY", "ticker": "AAPL", "usd_amount": 5000}]')
    risk, actions, transcript = run_debate(llm, CONTEXT)
    assert actions[0]["ticker"] == "AAPL"
    assert risk is None
    assert llm.calls == 4
    assert transcript["analyst"] == "analyst report"
    assert transcript["bull"] == "bull case"
    assert transcript["bear"] == "bear case"


def test_run_debate_returns_three_values():
    from analysis.pipeline import run_debate
    llm = FakeLLM("analyst report", "bull case", "bear case",
                  '{"risk_assessment": {"stance": "aggressive", "cash_target_pct": 10, "default_position_pct": 18, "reasoning": "bull run"}, "actions": [{"action": "BUY", "ticker": "AAPL", "usd_amount": 5000}]}')
    risk, actions, transcript = run_debate(llm, CONTEXT)
    assert risk["stance"] == "aggressive"
    assert actions[0]["ticker"] == "AAPL"
    assert transcript["analyst"] == "analyst report"


# --- Comment reply pipeline ---

COMMENT_CTX = {"comments": [{"comment_id": "c1", "body": "Q", "trade_ticker": "X",
               "trade_action": "BUY", "trade_why": "W", "comments_count": 1,
               "age_label": "1h ago"}]}


def test_run_comment_replies_parses_first_try():
    from analysis.pipeline import run_comment_replies
    llm = FakeLLM('[{"comment_id": "c1", "reply": "Good question, here is why."}]')
    replies = run_comment_replies(llm, COMMENT_CTX)
    assert len(replies) == 1
    assert replies[0]["comment_id"] == "c1"


def test_run_comment_replies_retries_on_garbage():
    from analysis.pipeline import run_comment_replies
    llm = FakeLLM("no json here",
                  '[{"comment_id": "c1", "reply": "Retried and replied."}]')
    replies = run_comment_replies(llm, COMMENT_CTX)
    assert len(replies) == 1
    assert llm.calls == 2


def test_run_comment_replies_returns_empty_after_two_failures():
    from analysis.pipeline import run_comment_replies
    llm = FakeLLM("bad", "still bad")
    replies = run_comment_replies(llm, COMMENT_CTX)
    assert replies == []
    assert llm.calls == 2
