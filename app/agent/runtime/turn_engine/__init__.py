"""TurnEngine 包导出；子模块可直接 import，不经本文件急切拉取 engine"""

from __future__ import annotations

from typing import Any

__all__ = [
    "ModelToken",
    "PreparedTurn",
    "RecoveryHook",
    "ToolFinished",
    "ToolStarted",
    "TurnCompleted",
    "TurnEnded",
    "TurnEngine",
    "TurnEngineConfig",
    "TurnEngineInput",
    "TurnEvent",
    "TurnEventKind",
    "TurnFailed",
    "TurnInterrupted",
    "TurnStarted",
    "TurnStarting",
    "TurnSubscriber",
    "stream_agent_turn",
    "stream_prepared_turn",
]


def __getattr__(name: str) -> Any:
    """按名懒加载公开符号，避免 import 子模块时拖入 engine→runner 环"""
    if name in {"TurnEngine", "TurnEngineConfig", "TurnEngineInput"}:
        from app.agent.runtime.turn_engine.engine import (
            TurnEngine,
            TurnEngineConfig,
            TurnEngineInput,
        )

        return {
            "TurnEngine": TurnEngine,
            "TurnEngineConfig": TurnEngineConfig,
            "TurnEngineInput": TurnEngineInput,
        }[name]
    if name in {
        "ModelToken",
        "ToolFinished",
        "ToolStarted",
        "TurnCompleted",
        "TurnEnded",
        "TurnEvent",
        "TurnEventKind",
        "TurnFailed",
        "TurnInterrupted",
        "TurnStarted",
        "TurnStarting",
    }:
        from app.agent.runtime.turn_engine import events as _events

        return getattr(_events, name)
    if name == "RecoveryHook":
        from app.agent.runtime.turn_engine.handlers import RecoveryHook

        return RecoveryHook
    if name == "PreparedTurn":
        from app.agent.runtime.turn_engine.prepared import PreparedTurn

        return PreparedTurn
    if name == "TurnSubscriber":
        from app.agent.runtime.turn_engine.subscribers import TurnSubscriber

        return TurnSubscriber
    if name == "stream_agent_turn":
        from app.agent.runtime.turn_engine.entry import stream_agent_turn

        return stream_agent_turn
    if name == "stream_prepared_turn":
        from app.agent.runtime.turn_engine.entry import stream_prepared_turn

        return stream_prepared_turn
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    """列出懒导出符号"""
    return sorted({*globals().keys(), *__all__})
