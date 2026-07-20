from __future__ import annotations

from typing import Any


def sanitize_tool_schema(tool: dict[str, Any]) -> dict[str, Any]:
    if tool.get("type") != "function":
        return tool
    fn = tool.get("function") or {}
    params = fn.get("parameters") or {}
    if not isinstance(params, dict):
        return tool

    properties = dict(params.get("properties") or {})
    properties.pop("tool_call_id", None)
    params["properties"] = properties

    required = [key for key in params.get("required") or [] if key != "tool_call_id"]
    params["required"] = required
    fn["parameters"] = params
    tool["function"] = fn
    return tool
