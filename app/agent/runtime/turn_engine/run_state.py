from __future__ import annotations

from dataclasses import dataclass, field

from app.agent.runtime.turn.enums import TurnTerminatedBy


@dataclass
class RunState:
    answer_parts: list[str] = field(default_factory=list)
    tool_calls_count: int = 0
    terminated_by: TurnTerminatedBy = TurnTerminatedBy.TURN_FAILED
    agent_started: bool = False
    failed_emitted: bool = False
