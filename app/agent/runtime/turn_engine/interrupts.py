from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph.state import CompiledStateGraph

from app.agent.runtime.turn_engine.interrupt_model import parse_pending_tool_actions


async def collect_interrupt_values(
    agent: CompiledStateGraph,
    config: RunnableConfig,
) -> list[Any]:
    snapshot = await agent.aget_state(config)
    if not snapshot.interrupts:
        return []
    values: list[Any] = []
    for interrupt in snapshot.interrupts:
        value = interrupt.value if hasattr(interrupt, "value") else interrupt
        values.append(value)
    return values


async def collect_pending_tool_calls(
    agent: CompiledStateGraph,
    config: RunnableConfig,
) -> list[dict[str, Any]]:
    snapshot = await agent.aget_state(config)
    raw_messages = snapshot.values.get("messages")
    if not isinstance(raw_messages, list):
        return []
    latest_ai: AIMessage | None = None
    for message in reversed(raw_messages):
        if isinstance(message, AIMessage):
            latest_ai = message
            break
    if latest_ai is None or not latest_ai.tool_calls:
        return []
    pending: list[dict[str, Any]] = []
    for call in latest_ai.tool_calls:
        if not isinstance(call, dict):
            continue
        call_id = call.get("id")
        name = call.get("name")
        args = call.get("args")
        if not isinstance(call_id, str) or not call_id:
            continue
        if not isinstance(name, str) or not name:
            continue
        pending.append(
            {
                "call_id": call_id,
                "name": name,
                "args": dict(args) if isinstance(args, dict) else {},
            }
        )
    return pending


def parse_interrupt_tool_pending(
    interrupts: list[Any],
    *,
    pending_tool_calls: list[Any] | None = None,
) -> list[dict[str, str]]:
    """Backward-compatible dict projection of tool-approval pending frames."""
    actions = parse_pending_tool_actions(
        interrupts,
        pending_tool_calls=pending_tool_calls,
    )
    return [
        {"call_id": action.call_id, "name": action.name, "summary": action.summary}
        for action in actions
    ]
