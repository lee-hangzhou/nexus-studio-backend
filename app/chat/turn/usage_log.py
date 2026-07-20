"""Append-only JSONL log of per-turn resource consumption and tool trace."""

from __future__ import annotations

import json
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.contracts.metadata import TurnUsageRecord
from app.core.config import settings
from app.core.logger import logger
from app.domain.chat.enums import RecoveryOutcome, TerminationReason, TurnStreamPhase

TerminatedBy = TerminationReason
_PREVIEW_LIMIT = 2000
_EXCEPTION_STACK_LIMIT = 8_000


@dataclass
class TurnUsageCollector:
    conversation_id: int
    turn_id: str
    model_key: str
    limits: dict[str, int]
    stream_phase: TurnStreamPhase = TurnStreamPhase.MAIN
    gate_id: str | None = None
    resume_action: str | None = None
    attachments: list[str] = field(default_factory=list)
    model_steps: list[dict[str, Any]] = field(default_factory=list)
    tools: list[dict[str, Any]] = field(default_factory=list)
    terminated_by: TerminatedBy = TerminationReason.ERROR
    termination_message: str | None = None
    exception_type: str | None = None
    exception_message: str | None = None
    exception_stack: str | None = None
    tool_recovery_count: int = 0
    empty_recovery_attempts: int = 0
    recovery_outcome: RecoveryOutcome = RecoveryOutcome.NOT_USED
    recovery_failure_reason: str | None = None
    _tool_index: int = 0
    _pending_starts: dict[str, tuple[float, int, str, dict[str, Any]]] = field(default_factory=dict)

    def note_model_step(
        self,
        *,
        step_index: int,
        tool_calls: list[str],
        content_len: int,
    ) -> None:
        self.model_steps.append(
            {
                "step_index": step_index,
                "tool_calls": tool_calls,
                "content_len": content_len,
            }
        )

    def note_tool_start(
        self,
        *,
        step_index: int,
        call_id: str,
        name: str,
        args: dict[str, Any],
    ) -> None:
        key = call_id or f"pending-{self._tool_index}-{name}"
        self._pending_starts[key] = (time.monotonic(), step_index, name, args)

    def note_tool_finish(
        self,
        *,
        step_index: int,
        call_id: str,
        name: str,
        args: dict[str, Any] | None,
        ok: bool,
        error_type: str | None,
        preview: str,
    ) -> None:
        key = call_id or f"pending-{self._tool_index}-{name}"
        started = self._pending_starts.pop(key, None)
        if started is None:
            for pending_key, value in list(self._pending_starts.items()):
                if value[2] == name:
                    started = self._pending_starts.pop(pending_key)
                    break
        duration_ms = None
        resolved_args = args or {}
        resolved_step = step_index
        if started is not None:
            duration_ms = round((time.monotonic() - started[0]) * 1000, 2)
            resolved_step = started[1]
            if not resolved_args:
                resolved_args = started[3]

        self.tools.append(
            {
                "index": self._tool_index,
                "step_index": resolved_step,
                "call_id": call_id,
                "name": name,
                "args": resolved_args,
                "ok": ok,
                "error_type": error_type,
                "preview": preview[:_PREVIEW_LIMIT],
                "duration_ms": duration_ms,
            }
        )
        self._tool_index += 1

    def note_termination(self, *, terminated_by: TerminatedBy, message: str | None) -> None:
        self.terminated_by = terminated_by
        if message:
            self.termination_message = message

    def note_tool_recovery(self) -> None:
        self.tool_recovery_count += 1
        self.recovery_outcome = RecoveryOutcome.IN_PROGRESS

    def note_tool_recovery_corrected(self) -> None:
        self.recovery_outcome = RecoveryOutcome.SUCCEEDED
        self.recovery_failure_reason = None

    def note_empty_recovery(
        self,
        *,
        attempts: int,
        succeeded: bool,
        failure_reason: str | None,
    ) -> None:
        self.empty_recovery_attempts += attempts
        self.recovery_outcome = (
            RecoveryOutcome.SUCCEEDED if succeeded else RecoveryOutcome.FAILED
        )
        self.recovery_failure_reason = failure_reason

    def note_failure(
        self,
        exc: BaseException | None = None,
        *,
        message: str | None = None,
    ) -> None:
        if exc is not None:
            self.exception_type = type(exc).__name__
            self.exception_message = str(exc)
            stack = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
            if len(stack) > _EXCEPTION_STACK_LIMIT:
                stack = stack[-_EXCEPTION_STACK_LIMIT:]
            self.exception_stack = stack
        elif message:
            self.exception_message = message[:2000]

    def first_error(self) -> dict[str, Any] | None:
        for item in self.tools:
            if not item.get("ok"):
                return {
                    "tool_index": item["index"],
                    "name": item["name"],
                    "error_type": item.get("error_type"),
                    "preview": item.get("preview"),
                }
        return None

    def to_record(
        self,
        *,
        model_steps_used: int,
        tool_calls_used: int,
        wall_clock_seconds: float,
    ) -> TurnUsageRecord:
        return TurnUsageRecord(
            conversation_id=self.conversation_id,
            turn_id=self.turn_id,
            model_key=self.model_key,
            stream_phase=self.stream_phase,
            gate_id=self.gate_id,
            resume_action=self.resume_action,
            model_steps_used=model_steps_used,
            tool_calls_used=tool_calls_used,
            wall_clock_seconds=round(wall_clock_seconds, 3),
            terminated_by=self.terminated_by,
            termination_message=self.termination_message,
            limits=self.limits,
            attachments=self.attachments,
            model_steps=self.model_steps,
            tools=self.tools,
            first_error=self.first_error(),
            exception_type=self.exception_type,
            exception_message=self.exception_message,
            exception_stack=self.exception_stack,
            tool_recovery_count=self.tool_recovery_count,
            empty_recovery_attempts=self.empty_recovery_attempts,
            recovery_outcome=self.recovery_outcome,
            recovery_failure_reason=self.recovery_failure_reason,
            timestamp=datetime.now(timezone.utc),
        )


def append_turn_usage_record(
    collector: TurnUsageCollector,
    *,
    model_steps_used: int,
    tool_calls_used: int,
    wall_clock_seconds: float,
) -> None:
    path = Path(settings.CHAT_TURN_USAGE_LOG_PATH)
    record = collector.to_record(
        model_steps_used=model_steps_used,
        tool_calls_used=tool_calls_used,
        wall_clock_seconds=wall_clock_seconds,
    )
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(record.model_dump(mode="json"), ensure_ascii=False) + "\n"
            )
    except OSError:
        logger.exception(
            "chat.turn.usage_log.write_failed",
            conversation_id=collector.conversation_id,
            turn_id=collector.turn_id,
            path=str(path),
        )
        return

    log_kwargs = {
        "conversation_id": collector.conversation_id,
        "turn_id": collector.turn_id,
        "path": str(path),
        "terminated_by": collector.terminated_by,
        "termination_message": collector.termination_message,
        "tool_count": len(collector.tools),
        "first_error": collector.first_error(),
        "exception_type": collector.exception_type,
        "exception_message": collector.exception_message,
    }
    if collector.terminated_by == "error":
        logger.error("chat.turn.usage_log.appended", **log_kwargs)
    else:
        logger.info("chat.turn.usage_log.appended", **log_kwargs)
