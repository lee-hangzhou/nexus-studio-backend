"""Map LangGraph user-gate interrupts to SSE frames."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from langgraph.graph.state import CompiledStateGraph
from langchain_core.runnables import RunnableConfig

from app.chat.gate.fields import normalize_field_defs
from app.chat.gate.pending import set_gate_pending
from app.chat.gate.public_payload import public_login_choices, strip_public_gate_payload
from app.chat.stream.frames import StreamFrame, StreamFrameType, create_stream_frame
from app.core.config import settings


def interrupt_value_is_user_gate(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    return bool(value.get("gate_type"))


async def has_pending_user_gate(agent: CompiledStateGraph, config: RunnableConfig) -> bool:
    snap = await agent.aget_state(config)
    if not snap.interrupts:
        return False
    for intr in snap.interrupts:
        value = intr.value if hasattr(intr, "value") else intr
        if interrupt_value_is_user_gate(value):
            return True
    return False


async def emit_user_gates(
    agent: CompiledStateGraph,
    config: RunnableConfig,
    emit: Callable[[StreamFrame], Awaitable[None]],
    *,
    turn_id: str,
    conversation_id: int,
    model_key: str,
    workspace: Path | None = None,
) -> bool:
    del workspace  # reserved for future asset hooks
    snap = await agent.aget_state(config)
    if not snap.interrupts:
        return False
    emitted = False
    for intr in snap.interrupts:
        value = intr.value if hasattr(intr, "value") else intr
        if not isinstance(value, dict):
            continue
        gate_id = str(value.get("gate_id") or "")
        gate_type_raw = value.get("gate_type")
        if not gate_type_raw:
            continue
        gate_type = str(gate_type_raw)
        phase_raw = value.get("phase")
        phase = str(phase_raw) if phase_raw is not None else None
        public = strip_public_gate_payload(dict(value))
        assets: dict[str, Any] = public.get("assets") if isinstance(public.get("assets"), dict) else {}
        raw_choices = public.get("choices") if isinstance(public.get("choices"), list) else []
        choices = public_login_choices(raw_choices)

        await emit(
            create_stream_frame(
                type=StreamFrameType.USER_GATE_REQUIRED,
                protocol_version=settings.CHAT_SSE_PROTOCOL_VERSION,
                turn_id=turn_id,
                gate_id=gate_id,
                gate_type=gate_type,
                prompt=str(public.get("prompt") or ""),
                fields=normalize_field_defs(
                    public.get("fields") if isinstance(public.get("fields"), list) else []
                ),
                assets=assets,
                choices=choices,
                phase=phase,
                domain=str(public.get("domain") or "") or None,
            )
        )
        if gate_id:
            await set_gate_pending(
                conversation_id,
                turn_id=turn_id,
                gate_id=gate_id,
                gate_type=gate_type,
                model_key=model_key,
                prompt=str(public.get("prompt") or ""),
                fields=normalize_field_defs(
                    public.get("fields") if isinstance(public.get("fields"), list) else []
                ),
                choices=choices,
                phase=phase,
                assets=assets,
                status="pending",
                domain=str(public.get("domain") or "") or None,
            )
        emitted = True
    return emitted
