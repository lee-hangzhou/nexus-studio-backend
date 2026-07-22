from __future__ import annotations

import time
from contextvars import ContextVar
from typing import Any

from app.server.infra.logger import logger

_turn_id: ContextVar[str | None] = ContextVar("turn_trace_turn_id", default=None)
_conversation_id: ContextVar[int | None] = ContextVar("turn_trace_conversation_id", default=None)


def bind_turn_trace(*, turn_id: str, conversation_id: int) -> None:
    _turn_id.set(turn_id)
    _conversation_id.set(conversation_id)


def log_stage(stage: str, **fields: Any) -> None:
    payload: dict[str, Any] = {
        "stage": stage,
        "turn_id": _turn_id.get(),
        "conversation_id": _conversation_id.get(),
        **fields,
    }
    if "duration_ms" not in payload and "started" in payload:
        payload["duration_ms"] = round((time.perf_counter() - payload.pop("started")) * 1000, 2)
    logger.info("chat.turn.trace", **payload)
