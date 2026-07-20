from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any
from uuid import uuid4

from langchain_core.tools import BaseTool
from pydantic import ValidationError

from app.compat.agent_tools.models import (
    RecoveredToolCall,
    RecoveryParserKind,
    ToolCallRecoveryResult,
)

_FUNCTION_CALLS_RE = re.compile(
    r"<function_calls\b[^>]*>.*?</function_calls>",
    re.DOTALL | re.IGNORECASE,
)
_INVOKE_RE = re.compile(
    r'<invoke\s+name=["\']([^"\']+)["\']\s*>(.*?)</invoke>',
    re.DOTALL | re.IGNORECASE,
)
_PARAM_RE = re.compile(
    r'<parameter\s+name=["\']([^"\']+)["\']\s*>(.*?)</parameter>',
    re.DOTALL | re.IGNORECASE,
)
_METHOD_RE = re.compile(r'\{\s*"method"\s*:\s*"([^"]+)"', re.IGNORECASE)
_OPENAI_NAME_RE = re.compile(r'"name"\s*:\s*"([^"]+)"', re.IGNORECASE)

ARGUMENT_RENAMES: dict[str, dict[str, str]] = {
    "apply_canvas_patch": {
        "operations": "ops",
        "operation_list": "ops",
    },
}


def recover_tool_calls(
    text: str,
    *,
    tools_by_name: Mapping[str, BaseTool],
    enabled_parsers: Sequence[RecoveryParserKind],
) -> ToolCallRecoveryResult | None:
    if not text:
        return None
    parsers = {
        RecoveryParserKind.XML_INVOKE: _parse_xml_invoke,
        RecoveryParserKind.METHOD_JSON: _parse_method_json,
        RecoveryParserKind.OPENAI_JSON: _parse_openai_json,
    }
    for parser_kind in enabled_parsers:
        calls = parsers[parser_kind](text)
        if not calls:
            continue
        validated = _validate_calls(calls, tools_by_name=tools_by_name)
        if validated is None:
            return ToolCallRecoveryResult(
                parser_kind=parser_kind,
                calls=tuple(calls),
                raw_length=len(text),
                schema_valid=False,
                failure_reason="tool_schema_validation_failed",
            )
        return ToolCallRecoveryResult(
            parser_kind=parser_kind,
            calls=tuple(validated),
            raw_length=len(text),
            schema_valid=True,
        )
    return None


def _parse_xml_invoke(text: str) -> list[RecoveredToolCall]:
    blocks = _FUNCTION_CALLS_RE.findall(text)
    search_in = "".join(blocks) if blocks else text
    calls: list[RecoveredToolCall] = []
    for match in _INVOKE_RE.finditer(search_in):
        name = match.group(1).strip()
        args = {
            param.group(1).strip(): _parse_scalar_or_json(param.group(2))
            for param in _PARAM_RE.finditer(match.group(2))
            if param.group(1).strip()
        }
        calls.append(_build_call(name, args))
    return calls


def _parse_method_json(text: str) -> list[RecoveredToolCall]:
    calls: list[RecoveredToolCall] = []
    for match in _METHOD_RE.finditer(text):
        payload = _extract_json_object(text, match.start())
        if not isinstance(payload, dict):
            continue
        name = str(payload.get("method") or "").strip()
        params = payload.get("params")
        if name and isinstance(params, dict):
            calls.append(_build_call(name, params))
    return calls


def _parse_openai_json(text: str) -> list[RecoveredToolCall]:
    calls: list[RecoveredToolCall] = []
    for match in _OPENAI_NAME_RE.finditer(text):
        payload = _extract_json_object(text, text.rfind("{", 0, match.start() + 1))
        if not isinstance(payload, dict):
            continue
        name = str(payload.get("name") or "").strip()
        args = payload.get("arguments", payload.get("args"))
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                continue
        if name and isinstance(args, dict):
            calls.append(_build_call(name, args))
    return calls


def _build_call(name: str, args: dict[str, Any]) -> RecoveredToolCall:
    normalized = dict(args)
    payload = normalized.pop("payload", None)
    if isinstance(payload, dict):
        normalized = {**payload, **normalized}
    renames = ARGUMENT_RENAMES.get(name, {})
    normalized = {
        renames.get(key, key): value
        for key, value in normalized.items()
    }
    return RecoveredToolCall(
        call_id=f"recovered_{uuid4().hex[:12]}",
        name=name,
        args=normalized,
    )


def _validate_calls(
    calls: list[RecoveredToolCall],
    *,
    tools_by_name: Mapping[str, BaseTool],
) -> list[RecoveredToolCall] | None:
    for call in calls:
        tool = tools_by_name.get(call.name)
        if tool is None:
            return None
        try:
            tool.get_input_schema().model_validate(call.args)
        except ValidationError:
            return None
    return calls


def _parse_scalar_or_json(raw: str) -> Any:
    value = raw.strip()
    if not value:
        return ""
    if value.startswith(("{", "[")):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    if value.isdigit():
        return int(value)
    return value


def _extract_json_object(text: str, start: int) -> dict[str, Any] | None:
    if start < 0 or start >= len(text) or text[start] != "{":
        return None
    decoder = json.JSONDecoder()
    try:
        payload, _ = decoder.raw_decode(text[start:])
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None
