"""Map LangGraph user-gate interrupts to SSE frames."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Literal

from langgraph.graph.state import CompiledStateGraph
from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.agent.chat.gate.fields import normalize_field_defs
from app.agent.chat.gate.pending import set_gate_pending
from app.agent.chat.gate.public_payload import public_login_choices, strip_public_gate_payload
from app.agent.chat.stream.frames import StreamFrame, StreamFrameType, create_stream_frame
from app.server.infra.config import settings


class UserGateInterrupt(BaseModel):
    model_config = ConfigDict(extra="allow")

    gate_id: str = Field(min_length=1)
    gate_type: Literal[
        "login_method",
        "credentials",
        "phone_otp",
        "qr_scan",
        "image_captcha",
        "confirm",
        "session_bridge",
    ]
    prompt: str = Field(min_length=1)
    fields: list[dict[str, Any]]
    assets: dict[str, Any]
    choices: list[dict[str, Any]] | None


def _parse_user_gate_interrupt(value: object) -> UserGateInterrupt | None:
    if not isinstance(value, dict) or "gate_type" not in value:
        return None
    try:
        return UserGateInterrupt.model_validate(value)
    except ValidationError as exc:
        raise ValueError("user gate interrupt violates contract") from exc


def interrupt_value_is_user_gate(value: object) -> bool:
    return _parse_user_gate_interrupt(value) is not None


async def has_pending_user_gate(agent: CompiledStateGraph, config: RunnableConfig) -> bool:
    snap = await agent.aget_state(config)
    if not snap.interrupts:
        return False
    for intr in snap.interrupts:
        value = intr.value if hasattr(intr, "value") else intr
        if _parse_user_gate_interrupt(value) is not None:
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
        gate = _parse_user_gate_interrupt(value)
        if gate is None:
            continue
        # gate 已在 _parse_user_gate_interrupt 用 Pydantic 校验
        public = strip_public_gate_payload(gate.model_dump(mode="python"))
        phase_raw = public.get("phase")
        phase = str(phase_raw) if phase_raw is not None else None
        domain = str(public.get("domain") or "") or None
        assets = gate.assets
        choices = public_login_choices(gate.choices or [])
        fields = normalize_field_defs(gate.fields)

        await emit(
            create_stream_frame(
                type=StreamFrameType.USER_GATE_REQUIRED,
                protocol_version=settings.CHAT_SSE_PROTOCOL_VERSION,
                turn_id=turn_id,
                gate_id=gate.gate_id,
                gate_type=gate.gate_type,
                prompt=gate.prompt,
                fields=fields,
                assets=assets,
                choices=choices,
                phase=phase,
                domain=domain,
            )
        )
        await set_gate_pending(
            conversation_id,
            turn_id=turn_id,
            gate_id=gate.gate_id,
            gate_type=gate.gate_type,
            model_key=model_key,
            prompt=gate.prompt,
            fields=fields,
            choices=choices,
            phase=phase,
            assets=assets,
            status="pending",
            domain=domain,
        )
        emitted = True
    return emitted
