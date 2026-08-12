import pytest

from analysis.parsing import JudgeParseError, parse_judge_actions, parse_judge_response


def test_parses_plain_array():
    out = parse_judge_actions('[{"action": "BUY", "ticker": "AAPL"}]')
    assert out == [{"action": "BUY", "ticker": "AAPL"}]


def test_parses_fenced_json():
    text = 'Here is my decision:\n```json\n[{"action": "SELL", "ticker": "MSFT"}]\n```\nDone.'
    out = parse_judge_actions(text)
    assert out[0]["ticker"] == "MSFT"


def test_parses_array_amid_prose():
    text = 'I think we should act. [{"action": "BUY", "ticker": "NVDA"}] is my call.'
    assert parse_judge_actions(text)[0]["ticker"] == "NVDA"


def test_empty_array():
    assert parse_judge_actions("[]") == []


def test_raises_on_garbage():
    with pytest.raises(JudgeParseError):
        parse_judge_actions("I cannot decide today.")


def test_raises_on_malformed_json():
    with pytest.raises(JudgeParseError):
        parse_judge_actions('[{"action": "BUY", ]')


def test_parses_risk_assessment_with_actions():
    text = '{"risk_assessment": {"stance": "defensive", "cash_target_pct": 35, "default_position_pct": 8, "reasoning": "high CPI"}, "actions": [{"action": "BUY", "ticker": "AAPL"}]}'
    risk, actions = parse_judge_response(text)
    assert risk["stance"] == "defensive"
    assert risk["cash_target_pct"] == 35
    assert actions[0]["ticker"] == "AAPL"


def test_parses_plain_array_as_fallback():
    risk, actions = parse_judge_response('[{"action": "BUY", "ticker": "MSFT"}]')
    assert risk is None
    assert actions[0]["ticker"] == "MSFT"


def test_parses_risk_response_amid_prose():
    text = 'Here is my decision:\n```json\n{"risk_assessment": {"stance": "neutral", "cash_target_pct": 20, "default_position_pct": 12, "reasoning": "stable"}, "actions": [{"action": "SELL", "ticker": "NVDA"}]}\n```'
    risk, actions = parse_judge_response(text)
    assert risk["stance"] == "neutral"
    assert actions[0]["ticker"] == "NVDA"


def test_parses_empty_actions_with_risk():
    text = '{"risk_assessment": {"stance": "defensive", "cash_target_pct": 50, "default_position_pct": 5, "reasoning": "crash"}, "actions": []}'
    risk, actions = parse_judge_response(text)
    assert risk["stance"] == "defensive"
    assert actions == []


# --- Comment reply parsing ---

from analysis.parsing import CommentReplyParseError, parse_comment_replies


def test_parse_comment_replies_plain_array():
    text = '[{"comment_id": "c1", "reply": "Good point, but I disagree."}]'
    result = parse_comment_replies(text)
    assert len(result) == 1
    assert result[0]["comment_id"] == "c1"
    assert "disagree" in result[0]["reply"]


def test_parse_comment_replies_multiple():
    text = '[{"comment_id": "c1", "reply": "Thanks!"}, {"comment_id": "c2", "reply": "Fair concern."}]'
    result = parse_comment_replies(text)
    assert len(result) == 2


def test_parse_comment_replies_amid_prose():
    text = 'Here are my replies:\n```json\n[{"comment_id": "c1", "reply": "Noted."}]\n```'
    result = parse_comment_replies(text)
    assert result[0]["comment_id"] == "c1"


def test_parse_comment_replies_raises_on_garbage():
    with pytest.raises(CommentReplyParseError):
        parse_comment_replies("I have nothing to say.")


def test_parse_comment_replies_raises_on_missing_fields():
    with pytest.raises(CommentReplyParseError):
        parse_comment_replies('[{"comment_id": "c1"}]')


def test_parse_comment_replies_raises_on_empty_array():
    with pytest.raises(CommentReplyParseError):
        parse_comment_replies("[]")
