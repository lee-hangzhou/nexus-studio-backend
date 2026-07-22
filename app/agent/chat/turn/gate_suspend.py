"""Persist structured outcomes for interrupted request_user_gate tool calls."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langgraph.graph.state import CompiledStateGraph
from langchain_core.runnables import RunnableConfig

from app.agent.chat.gate.observation_contract import build_gate_suspended_tool_content
from app.agent.chat.tools.browser_names import REQUEST_USER_GATE
from app.agent.chat.turn.gate_emit import interrupt_value_is_user_gate
from app.agent.chat.turn.observation import TurnObservationContext
from app.contracts.metadata import GateSuspendMetadata, ToolResultMetadata
from app.server.infra.logger import logger


@dataclass(frozen=True)
class UserGateInterrupt:
    gate_id: str
    gate_type: str


def _collect_user_gate_interrupts(state_snapshot: Any) -> list[UserGateInterrupt]:
    interrupts = list(getattr(state_snapshot, "interrupts", None) or [])
    out: list[UserGateInterrupt] = []
    for intr in interrupts:
        value = intr.value if hasattr(intr, "value") else intr
        if not interrupt_value_is_user_gate(value):
            continue
        gate_id = str(value.get("gate_id") or "").strip()
        gate_type = str(value.get("gate_type") or "").strip()
        if gate_id and gate_type:
            out.append(UserGateInterrupt(gate_id=gate_id, gate_type=gate_type))
    return out


def _fulfilled_tool_call_ids(messages: list[BaseMessage]) -> set[str]:
    fulfilled: set[str] = set()
    for message in messages:
        if isinstance(message, ToolMessage) and message.tool_call_id:
            fulfilled.add(str(message.tool_call_id))
    return fulfilled


def _unmatched_request_user_gate_call_ids(messages: list[BaseMessage]) -> list[str]:
    fulfilled = _fulfilled_tool_call_ids(messages)
    unmatched: list[str] = []
    for message in reversed(messages):
        if not isinstance(message, AIMessage):
            continue
        for call in message.tool_calls or []:
            call_id = str(call.get("id") or "").strip()
            name = str(call.get("name") or "").strip()
            if name == REQUEST_USER_GATE and call_id and call_id not in fulfilled:
                unmatched.append(call_id)
        if unmatched:
            break
    return list(reversed(unmatched))


async def persist_gate_suspend_tool_results(
    *,
    agent: CompiledStateGraph,
    config: RunnableConfig,
    observation: TurnObservationContext,
) -> list[int]:
    snap = await agent.aget_state(config)
    interrupts = _collect_user_gate_interrupts(snap)
    if not interrupts:
        return []
    messages = list(snap.values.get("messages") or [])
    unmatched_call_ids = _unmatched_request_user_gate_call_ids(messages)
    if not unmatched_call_ids:
        return []

    message_ids: list[int] = []
    for index, call_id in enumerate(unmatched_call_ids):
        gate = interrupts[min(index, len(interrupts) - 1)]
        content = build_gate_suspended_tool_content(
            gate_id=gate.gate_id,
            gate_type=gate.gate_type,
        )
        row_id = await observation.persistence.persist_gate_suspended_result(
            turn_id=observation.turn_id,
            call_id=call_id,
            content=content,
            meta=ToolResultMetadata(
                name=REQUEST_USER_GATE,
                call_id=call_id,
                synthetic=True,
                stream=observation.stream_meta,
                gate_suspend=GateSuspendMetadata(
                    gate_id=gate.gate_id,
                    gate_type=gate.gate_type,
                ),
            ),
        )
        observation.usage_collector.note_tool_finish(
            step_index=0,
            call_id=call_id,
            name=REQUEST_USER_GATE,
            args=None,
            ok=True,
            error_type=None,
            preview=content,
        )
        message_ids.append(row_id)
        logger.info(
            "chat.turn.gate_suspend_persisted",
            conversation_id=observation.conversation_id,
            turn_id=observation.turn_id,
            gate_id=gate.gate_id,
            gate_type=gate.gate_type,
            call_id=call_id,
            stream_phase=observation.phase,
        )
    return message_ids
