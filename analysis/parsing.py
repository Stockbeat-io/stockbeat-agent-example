import json


class JudgeParseError(Exception):
    pass


def parse_judge_response(text: str) -> tuple[dict | None, list]:
    if text is None:
        raise JudgeParseError("no text")

    # Try object format first: {"risk_assessment": ..., "actions": [...]}
    obj_start = text.find("{")
    obj_end = text.rfind("}")
    if obj_start != -1 and obj_end > obj_start:
        try:
            parsed = json.loads(text[obj_start : obj_end + 1])
            if isinstance(parsed, dict) and "actions" in parsed:
                actions = parsed["actions"]
                if not isinstance(actions, list):
                    raise JudgeParseError("actions is not a list")
                risk = parsed.get("risk_assessment")
                return risk, actions
        except json.JSONDecodeError:
            pass

    # Fallback: plain array
    arr_start = text.find("[")
    arr_end = text.rfind("]")
    if arr_start == -1 or arr_end == -1 or arr_end < arr_start:
        raise JudgeParseError("no JSON array found")

    try:
        parsed = json.loads(text[arr_start : arr_end + 1])
    except json.JSONDecodeError as exc:
        raise JudgeParseError(f"invalid JSON: {exc}") from exc

    if not isinstance(parsed, list):
        raise JudgeParseError("parsed value is not a list")
    return None, parsed


def parse_judge_actions(text: str) -> list:
    _, actions = parse_judge_response(text)
    return actions


class CommentReplyParseError(Exception):
    pass


def parse_comment_replies(text: str) -> list[dict]:
    if text is None:
        raise CommentReplyParseError("no text")

    arr_start = text.find("[")
    arr_end = text.rfind("]")
    if arr_start == -1 or arr_end == -1 or arr_end < arr_start:
        raise CommentReplyParseError("no JSON array found")

    try:
        parsed = json.loads(text[arr_start : arr_end + 1])
    except json.JSONDecodeError as exc:
        raise CommentReplyParseError(f"invalid JSON: {exc}") from exc

    if not isinstance(parsed, list) or not parsed:
        raise CommentReplyParseError("empty or non-list result")

    for item in parsed:
        if not isinstance(item, dict):
            raise CommentReplyParseError("item is not a dict")
        if "comment_id" not in item or "reply" not in item:
            raise CommentReplyParseError("missing comment_id or reply field")

    return parsed
