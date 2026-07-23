from app.agent.runtime.turn_engine.engine import TurnEngine, TurnEngineConfig, TurnEngineInput
from app.agent.runtime.turn_engine.events import (
    ModelToken,
    ToolFinished,
    ToolStarted,
    TurnCompleted,
    TurnEnded,
    TurnEvent,
    TurnEventKind,
    TurnFailed,
    TurnInterrupted,
    TurnStarted,
    TurnStarting,
)
from app.agent.runtime.turn_engine.handlers import RecoveryHook
from app.agent.runtime.turn_engine.prepared import PreparedTurn
from app.agent.runtime.turn_engine.subscribers import TurnSubscriber

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
]


def __getattr__(name: str):
    if name == "stream_agent_turn":
        from app.agent.runtime.turn_engine.entry import stream_agent_turn

        return stream_agent_turn
    if name == "stream_prepared_turn":
        from app.agent.runtime.turn_engine.entry import stream_prepared_turn

        return stream_prepared_turn
    raise AttributeError(name)
