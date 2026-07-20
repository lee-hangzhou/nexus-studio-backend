"""识别并清理模型输出中的伪工具标记。

工具调用恢复由 ``app.compat.agent_tools.parsers`` 负责；这里不再根据自然语言判断工具是否执行成功。
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from langchain_core.tools import BaseTool

from app.compat.agent_tools.models import RecoveryParserKind
from app.compat.agent_tools.parsers import recover_tool_calls

_FUNCTION_CALLS_RE = re.compile(
    r"<function_calls\b[^>]*>.*?</function_calls>",
    re.DOTALL | re.IGNORECASE,
)
_FUNCTION_RESPONSE_RE = re.compile(
    r"<function_response\b[^>]*>.*?</function_response>",
    re.DOTALL | re.IGNORECASE,
)
_FENCED_TOOL_JSON_RE = re.compile(
    r"```(?:json)?\s*([\s\S]*?(?:\"method\"|\"arguments\"|\"args\")[\s\S]*?)```",
    re.IGNORECASE,
)
_PROSE_TOOL_BLOCK_RE = re.compile(
    r"(?:\*\*)?(?:工具调用|Tool\s*call)(?:\*\*)?\s*[:：][\s\S]*?(?=\n\s*---|\n\n[^\s]|\Z)",
    re.IGNORECASE,
)
_INVOKE_TAG_RE = re.compile(
    r"<invoke\s+name\s*=",
    re.IGNORECASE,
)
_DSML_MARKER_RE = re.compile(
    r"(?:｜｜)?DSML(?:｜｜)?",
    re.IGNORECASE,
)
_PLAYWRIGHT_SELECTOR_IN_EVAL_RE = re.compile(
    r":has(?:-text)?\s*\(",
    re.IGNORECASE,
)


def strip_pseudo_tool_markup(text: str) -> str:
    if not text:
        return ""
    cleaned = _FUNCTION_RESPONSE_RE.sub("", text)
    cleaned = _FUNCTION_CALLS_RE.sub("", cleaned)
    cleaned = _PROSE_TOOL_BLOCK_RE.sub("", cleaned)
    cleaned = _FENCED_TOOL_JSON_RE.sub("", cleaned)
    if _DSML_MARKER_RE.search(cleaned):
        cleaned = re.sub(
            r"(?:｜｜)?DSML(?:｜｜)?[\s\S]*",
            "",
            cleaned,
            flags=re.IGNORECASE,
        )
    if _INVOKE_TAG_RE.search(cleaned):
        cleaned = re.sub(
            r"<invoke\b[^>]*>.*?(?:</invoke>|$)",
            "",
            cleaned,
            flags=re.DOTALL | re.IGNORECASE,
        )
    return cleaned.strip()


def content_looks_like_tool_hallucination(text: str) -> bool:
    if not text:
        return False
    return bool(
        _FUNCTION_CALLS_RE.search(text)
        or _FUNCTION_RESPONSE_RE.search(text)
        or _FENCED_TOOL_JSON_RE.search(text)
        or _INVOKE_TAG_RE.search(text)
        or _DSML_MARKER_RE.search(text)
    )


def browser_script_uses_playwright_selector_in_evaluate(code: str) -> bool:
    """Detect Playwright-only selectors near page.evaluate (invalid in querySelector)."""
    if not code or not re.search(r"\.evaluate\s*\(", code, re.IGNORECASE):
        return False
    for match in re.finditer(r"\.evaluate\s*\(", code, re.IGNORECASE):
        snippet = code[match.start() : match.start() + 4000]
        if _PLAYWRIGHT_SELECTOR_IN_EVAL_RE.search(snippet):
            return True
    return False


def recover_tool_calls_from_pseudo_markup(
    text: str,
    *,
    tools_by_name: Mapping[str, BaseTool],
    enabled_parsers: Sequence[RecoveryParserKind] = tuple(RecoveryParserKind),
) -> list[dict]:
    result = recover_tool_calls(
        text,
        tools_by_name=tools_by_name,
        enabled_parsers=enabled_parsers,
    )
    if result is None or not result.schema_valid:
        return []
    return [call.to_langchain_call() for call in result.calls]


def recover_tool_calls_from_model_text(
    *parts: str | None,
    tools_by_name: Mapping[str, BaseTool],
    enabled_parsers: Sequence[RecoveryParserKind] = tuple(RecoveryParserKind),
) -> list[dict]:
    for part in parts:
        if not part:
            continue
        calls = recover_tool_calls_from_pseudo_markup(
            part,
            tools_by_name=tools_by_name,
            enabled_parsers=enabled_parsers,
        )
        if calls:
            return calls
    return []
