"""Agent graph stream runner（LangGraph → typed AgentEvent）"""

from __future__ import annotations

from typing import Any

__all__ = [
    "AgentEvent",
    "AgentEventType",
    "AgentTurnInput",
    "run_agent_turn_stream",
]


def __getattr__(name: str) -> Any:
    """按名懒加载，避免 import 子模块时拖入 runner"""
    if name in {"AgentEvent", "AgentEventType"}:
        from app.agent.runtime.agent.events import AgentEvent, AgentEventType

        return {"AgentEvent": AgentEvent, "AgentEventType": AgentEventType}[name]
    if name in {"AgentTurnInput", "run_agent_turn_stream"}:
        from app.agent.runtime.agent.runner import AgentTurnInput, run_agent_turn_stream

        return {
            "AgentTurnInput": AgentTurnInput,
            "run_agent_turn_stream": run_agent_turn_stream,
        }[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    """列出懒导出符号"""
    return sorted({*globals().keys(), *__all__})
