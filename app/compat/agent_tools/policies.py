from __future__ import annotations

from typing import Any, Awaitable, Callable, Sequence, cast

from langchain.agents.middleware.types import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import BaseTool, StructuredTool
from langgraph.types import Overwrite
from pydantic import ValidationError

from app.chat.tools.result import INVALID_ARGUMENTS, ToolResult
from app.compat.agent_tools.models import ToolCallIssue

INTERNAL_TOOL_NAME = "__tool_call_error"


def _tool_call_args_schema(tool: BaseTool):
    schema = getattr(tool, "tool_call_schema", None)
    if schema is None:
        return tool.get_input_schema()
    return schema


def _required_fields(tool: BaseTool) -> tuple[str, ...]:
    schema = _tool_call_args_schema(tool).model_json_schema()
    return tuple(str(item) for item in schema.get("required", []))


def classify_tool_call(
    call: dict[str, Any],
    *,
    tools_by_name: dict[str, BaseTool],
) -> ToolCallIssue | None:
    call_id = str(call.get("id") or "")
    name = str(call.get("name") or "").strip()
    args = call.get("args")
    if name == INTERNAL_TOOL_NAME:
        return None
    if not name or name == "unknown" or name not in tools_by_name:
        return ToolCallIssue(
            call_id=call_id,
            tool_name=name or "unknown",
            error_code="unknown_tool",
            allowed_tools=tuple(sorted(tools_by_name)),
        )
    if not isinstance(args, dict):
        return ToolCallIssue(
            call_id=call_id,
            tool_name=name,
            error_code="invalid_argument_type",
            detail="tool arguments must be an object",
        )

    missing = tuple(
        field
        for field in _required_fields(tools_by_name[name])
        if args.get(field) is None
        or (isinstance(args.get(field), str) and not args[field].strip())
    )
    if missing:
        return ToolCallIssue(
            call_id=call_id,
            tool_name=name,
            error_code="missing_required_arg",
            fields=missing,
        )
    try:
        _tool_call_args_schema(tools_by_name[name]).model_validate(args)
    except ValidationError as exc:
        fields = tuple(
            str(error["loc"][0])
            for error in exc.errors()
            if error.get("loc")
        )
        return ToolCallIssue(
            call_id=call_id,
            tool_name=name,
            error_code="invalid_argument_type",
            fields=tuple(dict.fromkeys(fields)),
            detail="one or more arguments have an invalid type",
        )
    return None


def issue_to_internal_call(
    issue: ToolCallIssue,
    *,
    recovery_attempt: int,
) -> dict[str, Any]:
    return {
        "id": issue.call_id,
        "name": INTERNAL_TOOL_NAME,
        "type": "tool_call",
        "args": {
            "original_tool": issue.tool_name,
            "error_code": issue.error_code,
            "fields": list(issue.fields),
            "allowed_tools": list(issue.allowed_tools),
            "detail": issue.detail,
            "raw_length": issue.raw_length,
            "recovery_attempt": recovery_attempt,
        },
    }


def invalid_json_internal_call(
    *,
    call_id: str,
    tool_name: str,
    raw_length: int,
) -> dict[str, Any]:
    return issue_to_internal_call(
        ToolCallIssue(
            call_id=call_id,
            tool_name=tool_name or "unknown",
            error_code="invalid_json",
            detail="tool arguments were not valid JSON",
            raw_length=raw_length,
        ),
        recovery_attempt=1,
    )


async def _tool_call_error(
    original_tool: str,
    error_code: str,
    fields: list[str] | None = None,
    allowed_tools: list[str] | None = None,
    detail: str = "",
    raw_length: int = 0,
    recovery_attempt: int = 1,
) -> str:
    field_text = ", ".join(fields or []) or "未指定"
    feedback = (
        f"工具调用参数无效。工具={original_tool or 'unknown'}；"
        f"错误={error_code}；字段={field_text}。"
        "请修正参数后重新调用；如果无法修正，请基于已有信息直接回答用户。"
    )
    if allowed_tools:
        feedback += f" 可用工具={', '.join(allowed_tools)}。"
    if detail:
        feedback += f" 提示：{detail}。"
    if raw_length:
        feedback += f" 原始参数长度={raw_length}。"
    return cast(
        str,
        ToolResult.fail(
            INVALID_ARGUMENTS,
            detail=feedback,
            output="参数异常，正在自动修复",
        ).to_tool_message(),
    )


def build_internal_tool() -> StructuredTool:
    return StructuredTool.from_function(
        coroutine=_tool_call_error,
        name=INTERNAL_TOOL_NAME,
        description="Internal tool-call validation feedback. Never call this tool directly.",
    )


class ToolSelfHealMiddleware(AgentMiddleware):
    name = "tool_self_heal"

    def __init__(self, tools: Sequence[BaseTool]) -> None:
        self.tools_by_name = {
            tool.name: tool
            for tool in tools
            if tool.name != INTERNAL_TOOL_NAME
        }

    def _rewrite(self, state: dict[str, Any]) -> dict[str, Any] | None:
        messages = list(state.get("messages") or [])
        if not messages or not isinstance(messages[-1], AIMessage):
            return None
        message = messages[-1]
        calls = list(message.tool_calls or [])
        if not calls:
            return None
        prior_attempts = sum(
            1
            for item in messages[:-1]
            if isinstance(item, ToolMessage) and item.name == INTERNAL_TOOL_NAME
        )
        changed = False
        rewritten: list[dict[str, Any]] = []
        for call in calls:
            issue = classify_tool_call(call, tools_by_name=self.tools_by_name)
            if issue is None:
                rewritten.append(call)
                continue
            changed = True
            rewritten.append(
                issue_to_internal_call(
                    issue,
                    recovery_attempt=prior_attempts + 1,
                )
            )
        if not changed:
            return None
        messages[-1] = message.model_copy(update={"tool_calls": rewritten})
        return {"messages": Overwrite(messages)}

    def after_model(self, state: dict[str, Any], runtime: Any) -> dict[str, Any] | None:
        return self._rewrite(state)

    async def aafter_model(
        self,
        state: dict[str, Any],
        runtime: Any,
    ) -> dict[str, Any] | None:
        return self._rewrite(state)

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        visible_tools = [
            tool
            for tool in request.tools
            if getattr(tool, "name", None) != INTERNAL_TOOL_NAME
        ]
        return await handler(request.override(tools=visible_tools))
