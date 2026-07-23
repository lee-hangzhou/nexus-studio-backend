from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.agent.runtime.agent.tool_call_registry import unresolved_pending_tool_calls
from app.agent.runtime.tools.result import ToolResult


def test_unresolved_pending_tool_calls_from_latest_ai_message() -> None:
    messages = [
        HumanMessage(content="hi"),
        AIMessage(
            content="",
            tool_calls=[
                {
                    "id": "call_gate",
                    "name": "request_user_gate",
                    "args": {"gate_type": "login_method", "prompt": "choose"},
                }
            ],
        ),
    ]
    assert unresolved_pending_tool_calls(messages) == [
        ("call_gate", "request_user_gate", {"gate_type": "login_method", "prompt": "choose"}),
    ]


def test_unresolved_pending_tool_calls_skips_fulfilled() -> None:
    messages = [
        AIMessage(
            content="",
            tool_calls=[
                {"id": "call_1", "name": "web_search", "args": {"query": "x"}},
                {"id": "call_2", "name": "request_user_gate", "args": {"gate_type": "confirm", "prompt": "ok"}},
            ],
        ),
        ToolMessage(
            content=ToolResult.ok("{}").to_tool_message(),
            tool_call_id="call_1",
            name="web_search",
        ),
    ]
    assert unresolved_pending_tool_calls(messages) == [
        ("call_2", "request_user_gate", {"gate_type": "confirm", "prompt": "ok"}),
    ]
