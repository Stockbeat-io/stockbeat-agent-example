from analysis.parsing import CommentReplyParseError, JudgeParseError, parse_comment_replies, parse_judge_response
from analysis.prompts import build_analyst_prompt, build_bear_prompt, build_bull_prompt, build_comment_reply_prompt, build_judge_prompt
from config import get_logger

log = get_logger()

_RETRY = (
    "\n\nYour previous response could not be parsed. "
    "Respond with ONLY valid JSON and nothing else."
)


def run_analyst(llm, context: dict) -> str:
    system, user = build_analyst_prompt(context)
    return llm.generate(user, system=system) or ""


def run_bull(llm, context: dict, analyst_report: str) -> str:
    system, user = build_bull_prompt(context, analyst_report)
    return llm.generate(user, system=system) or ""


def run_bear(llm, context: dict, analyst_report: str, bull_case: str) -> str:
    system, user = build_bear_prompt(context, analyst_report, bull_case)
    return llm.generate(user, system=system) or ""


def run_judge(llm, context: dict) -> tuple[dict | None, list]:
    system, user = build_judge_prompt(context)
    text = llm.generate(user, system=system)
    try:
        return parse_judge_response(text)
    except JudgeParseError:
        log.info("LLM:JUDGE | parse failed, retrying once")

    text = llm.generate(user + _RETRY, system=system)
    try:
        return parse_judge_response(text)
    except JudgeParseError:
        log.info("LLM:JUDGE | parse failed again, returning no actions")
        return None, []


def run_comment_replies(llm, context: dict) -> list[dict]:
    system, user = build_comment_reply_prompt(context)
    text = llm.generate(user, system=system)
    try:
        return parse_comment_replies(text)
    except CommentReplyParseError:
        log.info("LLM:COMMENTS | parse failed, retrying once")

    text = llm.generate(user + _RETRY, system=system)
    try:
        return parse_comment_replies(text)
    except CommentReplyParseError:
        log.info("LLM:COMMENTS | parse failed again, returning no replies")
        return []


def run_debate(llm, context: dict) -> tuple[dict | None, list, dict]:
    """Returns (risk_assessment, actions, transcript)."""
    analyst = run_analyst(llm, context)
    bull = run_bull(llm, context, analyst)
    bear = run_bear(llm, context, analyst, bull)
    enriched = dict(context, analyst=analyst, bull=bull, bear=bear)
    risk, actions = run_judge(llm, enriched)
    transcript = {"analyst": analyst, "bull": bull, "bear": bear}
    return risk, actions, transcript
