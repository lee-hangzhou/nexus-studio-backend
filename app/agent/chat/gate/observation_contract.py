"""Structured gate tool outcomes for persistence and tracing."""

from __future__ import annotations

import json
from enum import StrEnum
from typing import Any


class GateToolResultStatus(StrEnum):
    SUSPENDED = "gate_suspended"


def build_gate_suspended_tool_content(*, gate_id: str, gate_type: str) -> str:
    payload: dict[str, Any] = {
        "status": GateToolResultStatus.SUSPENDED,
        "gate_id": gate_id,
        "gate_type": gate_type,
    }
    return json.dumps(payload, ensure_ascii=False)
