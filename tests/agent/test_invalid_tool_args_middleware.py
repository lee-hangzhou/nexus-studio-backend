"""非法 tool 参数注回 LangGraph 消息态, 供同轮模型重试"""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.types import Overwrite

from app.agent.chat.agent.invalid_tool_args import (
    INVALID_TOOL_CALLS_KWARG,
    InvalidToolCallPayload,
    synthesize_invalid_tool_arg_messages,
)
from app.agent.chat.agent.invalid_tool_args_middleware import InvalidToolArgsMiddleware
from app.agent.runtime.tools.result import INVALID_ARGUMENTS


def test_synthesize_invalid_tool_arg_messages_pairs_call_ids() -> None:
    """合成的 ToolMessage 必须配对 call_id, 且错误类型为 invalid_arguments"""
    messages = synthesize_invalid_tool_arg_messages(
        [
            InvalidToolCallPayload(
                call_id="call_1",
                name="execute_python",
                raw_arguments='{"code": "unterm',
                parse_error="streamed tool arguments are not valid JSON",
            )
        ]
    )
    assert len(messages) == 1
    msg = messages[0]
    assert isinstance(msg, ToolMessage)
    assert msg.tool_call_id == "call_1"
    assert msg.name == "execute_python"
    assert INVALID_ARGUMENTS in str(msg.content)
    assert "valid JSON" in str(msg.content)


def test_middleware_injects_tool_errors_and_jumps_to_model_when_only_invalid() -> None:
    """仅有非法 tool args 时, 注回失败 ToolMessage 并跳回 model, 不进 ToolNode"""
    middleware = InvalidToolArgsMiddleware()
    ai = AIMessage(
        content="",
        tool_calls=[],
        additional_kwargs={
            INVALID_TOOL_CALLS_KWARG: [
                {
                    "id": "call_1",
                    "name": "execute_python",
                    "args": '{"code": "unterm',
                    "error": "streamed tool arguments are not valid JSON",
                }
            ]
        },
    )
    result = middleware.after_model(
        {"messages": [HumanMessage(content="跑一下"), ai]},
        runtime=None,
    )
    assert result is not None
    assert result.get("jump_to") == "model"
    overwritten = result["messages"]
    assert isinstance(overwritten, Overwrite)
    messages = list(overwritten.value)
    assert isinstance(messages[-2], AIMessage)
    assert messages[-2].tool_calls
    assert messages[-2].tool_calls[0]["id"] == "call_1"
    assert isinstance(messages[-1], ToolMessage)
    assert messages[-1].tool_call_id == "call_1"
    assert messages[-1].status == "error"


def test_middleware_skips_when_valid_tool_calls_present() -> None:
    """同步仍有可执行 tool_calls 时不改写, 避免抢 ToolNode"""
    middleware = InvalidToolArgsMiddleware()
    ai = AIMessage(
        content="",
        tool_calls=[{"id": "ok", "name": "read_file", "args": {"path": "a.py"}}],
        additional_kwargs={
            INVALID_TOOL_CALLS_KWARG: [
                {
                    "id": "bad",
                    "name": "execute_python",
                    "args": "{",
                    "error": "not valid JSON",
                }
            ]
        },
    )
    assert middleware.after_model({"messages": [ai]}, runtime=None) is None


def test_middleware_noop_without_invalid_kwargs() -> None:
    """无非法 tool 元数据时不介入"""
    middleware = InvalidToolArgsMiddleware()
    ai = AIMessage(content="你好", tool_calls=[])
    assert middleware.after_model({"messages": [ai]}, runtime=None) is None
