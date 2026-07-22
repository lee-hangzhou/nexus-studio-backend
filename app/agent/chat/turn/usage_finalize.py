"""Finalize and append a turn usage segment (main or gate resume)."""

from __future__ import annotations

from app.agent.chat.turn.observation import TurnObservationContext
from app.agent.chat.turn.usage_log import TerminatedBy, append_turn_usage_record


def finalize_observation_segment(
    observation: TurnObservationContext,
    *,
    model_steps_used: int,
    tool_calls_used: int,
    wall_clock_seconds: float,
    terminated_by: TerminatedBy,
    termination_message: str | None = None,
) -> None:
    observation.usage_collector.stream_phase = observation.phase
    observation.usage_collector.gate_id = observation.gate_id
    observation.usage_collector.resume_action = observation.resume_action
    if observation.usage_collector.terminated_by != terminated_by:
        observation.usage_collector.note_termination(
            terminated_by=terminated_by,
            message=termination_message,
        )
    append_turn_usage_record(
        observation.usage_collector,
        model_steps_used=model_steps_used,
        tool_calls_used=tool_calls_used,
        wall_clock_seconds=wall_clock_seconds,
    )
