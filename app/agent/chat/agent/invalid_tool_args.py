"""非法 streamed tool 参数: 结构化载荷与 ToolMessage 合成

additional_kwargs 只能存可 JSON 序列化的 dict; 进程内用 InvalidToolCallPayload,
出入 kwargs 边界才转 dict (LC/Checkpointer 约束, 非旁路协议扩张)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from langchain_core.messages import AIMessage, ToolMessage

from app.agent.runtime.tools.result import INVALID_ARGUMENTS, ToolResult

# 挂在 AIMessage.additional_kwargs; 流式 chunk merge 会保留, LC 原生 invalid_tool_calls 会被 merge 丢掉
INVALID_TOOL_CALLS_KWARG = "invalid_tool_calls"

_INVALID_TOOL_RAW_PREVIEW_LEN = 200


@dataclass(frozen=True)
class InvalidToolCallPayload:
    """一条非法 tool 调用的可序列化载荷"""

    call_id: str
    name: str
    raw_arguments: str
    parse_error: str

    def to_kwargs_dict(self) -> dict[str, str]:
        """转成 additional_kwargs 用的纯 dict"""
        return {
            "id": self.call_id,
            "name": self.name,
            "args": self.raw_arguments,
            "error": self.parse_error,
        }

    @classmethod
    def from_kwargs_dict(cls, item: dict[str, Any]) -> InvalidToolCallPayload | None:
        """从 kwargs dict 解析; id/name 缺失则拒绝该条"""
        call_id = str(item.get("id") or "")
        name = str(item.get("name") or "")
        if not call_id or not name:
            return None
        return cls(
            call_id=call_id,
            name=name,
            raw_arguments=str(item.get("args") or ""),
            parse_error=str(item.get("error") or ""),
        )


def invalid_tool_calls_payload(
    items: list[Any],
) -> list[InvalidToolCallPayload]:
    """把 assembler InvalidToolCall 转成进程内载荷列表; 缺 id/name 的条目丢弃并应由上游保证不出现"""
    out: list[InvalidToolCallPayload] = []
    for item in items:
        call_id = str(getattr(item, "call_id", "") or "")
        name = str(getattr(item, "name", "") or "")
        if not call_id or not name:
            continue
        out.append(
            InvalidToolCallPayload(
                call_id=call_id,
                name=name,
                raw_arguments=str(getattr(item, "raw_arguments", "") or ""),
                parse_error=str(getattr(item, "parse_error", "") or ""),
            )
        )
    return out


def payload_to_kwargs(items: list[InvalidToolCallPayload]) -> list[dict[str, str]]:
    """载荷列表写入 additional_kwargs 前的 dict 形态"""
    return [item.to_kwargs_dict() for item in items]


def read_invalid_tool_calls_kwargs(message: AIMessage) -> list[InvalidToolCallPayload]:
    """从 AIMessage.additional_kwargs 读取非法 tool 调用; 形态不对则当作没有"""
    raw = message.additional_kwargs.get(INVALID_TOOL_CALLS_KWARG)
    if not isinstance(raw, list):
        return []
    out: list[InvalidToolCallPayload] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        parsed = InvalidToolCallPayload.from_kwargs_dict(item)
        if parsed is None:
            continue
        out.append(parsed)
    return out


def synthesize_invalid_tool_arg_messages(
    invalid_calls: list[InvalidToolCallPayload],
) -> list[ToolMessage]:
    """为非法 tool args 合成配对的失败 ToolMessage"""
    out: list[ToolMessage] = []
    for call in invalid_calls:
        detail = (
            f"参数解析失败: {call.parse_error}\n"
            f"raw: {call.raw_arguments[:_INVALID_TOOL_RAW_PREVIEW_LEN]}"
        )
        out.append(
            ToolMessage(
                content=ToolResult.fail(INVALID_ARGUMENTS, detail=detail).to_tool_message(),
                tool_call_id=call.call_id,
                name=call.name,
                status="error",
            )
        )
    return out


def stub_tool_calls_for_invalid(
    invalid_calls: list[InvalidToolCallPayload],
) -> list[dict[str, Any]]:
    """为非法调用生成占位 tool_calls, 以便与 ToolMessage 配对进网关历史"""
    return [
        {"id": call.call_id, "name": call.name, "args": {}}
        for call in invalid_calls
    ]
