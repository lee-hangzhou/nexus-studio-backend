"""Unified turn observation context for main stream and gate resume."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.contracts.metadata import (
    ToolAuditMetadata,
    TurnContextMetadata,
    TurnStreamObservationMetadata,
)
from app.chat.turn.persistence import TurnPersistence
from app.chat.turn.usage_log import TurnUsageCollector
from app.domain.chat.enums import TurnStreamPhase


@dataclass
class TurnObservationContext:
    user_id: int
    conversation_id: int
    turn_id: str
    model_key: str
    phase: TurnStreamPhase
    persistence: TurnPersistence
    usage_collector: TurnUsageCollector
    turn_context: TurnContextMetadata
    gate_id: str | None = None
    resume_action: str | None = None
    tool_audit: list[ToolAuditMetadata] = field(default_factory=list)

    @property
    def stream_meta(self) -> TurnStreamObservationMetadata:
        return TurnStreamObservationMetadata(
            phase=self.phase,
            gate_id=self.gate_id,
            resume_action=self.resume_action,
        )

    @classmethod
    def for_main_turn(
        cls,
        *,
        user_id: int,
        conversation_id: int,
        turn_id: str,
        model_key: str,
        persistence: TurnPersistence,
        usage_collector: TurnUsageCollector,
        turn_context: TurnContextMetadata,
        tool_audit: list[ToolAuditMetadata] | None = None,
    ) -> TurnObservationContext:
        return cls(
            user_id=user_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            model_key=model_key,
            phase=TurnStreamPhase.MAIN,
            persistence=persistence,
            usage_collector=usage_collector,
            turn_context=turn_context,
            tool_audit=list(tool_audit or []),
        )

    @classmethod
    def for_gate_resume(
        cls,
        *,
        user_id: int,
        conversation_id: int,
        turn_id: str,
        model_key: str,
        gate_id: str,
        resume_action: str,
        persistence: TurnPersistence,
        usage_collector: TurnUsageCollector,
        turn_context: TurnContextMetadata,
    ) -> TurnObservationContext:
        return cls(
            user_id=user_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            model_key=model_key,
            phase=TurnStreamPhase.RESUME,
            gate_id=gate_id,
            resume_action=resume_action,
            persistence=persistence,
            usage_collector=usage_collector,
            turn_context=turn_context,
        )

    @classmethod
    def from_snapshot(
        cls,
        *,
        user_id: int,
        conversation_id: int,
        turn_id: str,
        gate_id: str,
        resume_action: str,
        persistence: TurnPersistence,
        snapshot: dict[str, Any],
    ) -> TurnObservationContext:
        turn_context_raw = snapshot.get("turn_context")
        if not isinstance(turn_context_raw, dict):
            raise ValueError("turn observation snapshot missing turn_context")
        limits_raw = snapshot.get("limits")
        if not isinstance(limits_raw, dict):
            raise ValueError("turn observation snapshot missing limits")
        attachments_raw = snapshot.get("attachments")
        attachments = (
            [str(item) for item in attachments_raw]
            if isinstance(attachments_raw, list)
            else []
        )
        model_key = str(snapshot.get("model_key") or "").strip()
        if not model_key:
            raise ValueError("turn observation snapshot missing model_key")
        usage_collector = TurnUsageCollector(
            conversation_id=conversation_id,
            turn_id=turn_id,
            model_key=model_key,
            limits={str(k): int(v) for k, v in limits_raw.items()},
            attachments=attachments,
            stream_phase=TurnStreamPhase.RESUME,
            gate_id=gate_id,
            resume_action=resume_action,
        )
        return cls.for_gate_resume(
            user_id=user_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            model_key=model_key,
            gate_id=gate_id,
            resume_action=resume_action,
            persistence=persistence,
            usage_collector=usage_collector,
            turn_context=TurnContextMetadata.model_validate(turn_context_raw),
        )
