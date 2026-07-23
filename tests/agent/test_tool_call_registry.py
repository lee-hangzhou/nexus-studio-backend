from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.agent.runtime.agent.tool_call_registry import (
    ToolCallPhase,
    ToolCallRegistry,
    unresolved_pending_tool_calls,
)
from app.agent.runtime.tools.result import ToolResult
from app.server.infra.gateway_errors import GatewayChatError


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


def test_registry_model_start_end_lifecycle() -> None:
    reg = ToolCallRegistry()
    reg.register_from_model_step(
        [{"id": "c1", "name": "web_search", "args": {"query": "q"}}]
    )
    started = reg.on_start(call_id="c1", tool_name="web_search", args={"query": "q"})
    assert started.phase is ToolCallPhase.STARTED
    ended = reg.on_end(call_id="c1", tool_name="web_search")
    assert ended.call_id == "c1"
    assert ended.args == {"query": "q"}
    assert reg.open_entries() == []


def test_registry_seed_resume_start_without_model_step() -> None:
    reg = ToolCallRegistry()
    reg.seed_from_messages(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "call_gate",
                        "name": "request_user_gate",
                        "args": {"gate_type": "confirm", "prompt": "ok"},
                    }
                ],
            )
        ]
    )
    started = reg.on_start(
        call_id="",
        tool_name="request_user_gate",
        args={"gate_type": "confirm", "prompt": "ok"},
    )
    assert started.call_id == "call_gate"
    ended = reg.on_end(call_id="call_gate", tool_name="")
    assert ended.tool_name == "request_user_gate"


def test_registry_error_missing_metadata_single_started() -> None:
    reg = ToolCallRegistry()
    reg.register_from_model_step(
        [{"id": "c1", "name": "browser_exec_script", "args": {"code": "1"}}]
    )
    reg.on_start(call_id="c1", tool_name="browser_exec_script", args={"code": "1"})
    failed = reg.on_error(call_id="", tool_name="")
    assert failed.call_id == "c1"
    assert failed.tool_name == "browser_exec_script"
    assert reg.open_entries() == []


def test_registry_error_missing_metadata_ambiguous_raises() -> None:
    reg = ToolCallRegistry()
    reg.register_from_model_step(
        [
            {"id": "c1", "name": "browser_exec_script", "args": {}},
            {"id": "c2", "name": "web_search", "args": {}},
        ]
    )
    reg.on_start(call_id="c1", tool_name="browser_exec_script", args={})
    reg.on_start(call_id="c2", tool_name="web_search", args={})
    with pytest.raises(GatewayChatError, match="missing identity"):
        reg.on_error(call_id="", tool_name="")


def test_registry_error_unknown_name_raises() -> None:
    reg = ToolCallRegistry()
    reg.register_from_model_step(
        [{"id": "c1", "name": "web_search", "args": {}}]
    )
    reg.on_start(call_id="c1", tool_name="web_search", args={})
    with pytest.raises(GatewayChatError, match="missing name=other_tool"):
        reg.on_error(call_id="", tool_name="other_tool")


def test_registry_parallel_same_name_fifo_without_call_id() -> None:
    """LangGraph may omit tool_call_id when the model emits parallel same-name calls."""
    reg = ToolCallRegistry()
    reg.register_from_model_step(
        [
            {"id": "p0", "name": "publish_file", "args": {"path": "a"}},
            {"id": "p1", "name": "publish_file", "args": {"path": "b"}},
            {"id": "p2", "name": "publish_file", "args": {"path": "c"}},
            {"id": "p3", "name": "publish_file", "args": {"path": "d"}},
        ]
    )
    started_ids = [
        reg.on_start(call_id="", tool_name="publish_file", args={"path": letter}).call_id
        for letter in ("a", "b", "c", "d")
    ]
    assert started_ids == ["p0", "p1", "p2", "p3"]
    for call_id in started_ids:
        reg.on_end(call_id=call_id, tool_name="publish_file")
    assert reg.open_entries() == []


def test_registry_end_without_start_raises() -> None:
    reg = ToolCallRegistry()
    with pytest.raises(GatewayChatError, match="unknown call_id|no matching start|missing identity"):
        reg.on_end(call_id="missing", tool_name="web_search")
