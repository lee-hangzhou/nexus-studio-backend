from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage

from app.server.infra.gateway_errors import GatewayChatError


class ToolCallPhase(StrEnum):
    PENDING = "pending"
    STARTED = "started"


@dataclass
class ToolCallEntry:
    call_id: str
    tool_name: str
    phase: ToolCallPhase
    args: dict[str, Any] = field(default_factory=dict)


def unresolved_pending_tool_calls(messages: list[BaseMessage]) -> list[tuple[str, str, dict[str, Any]]]:
    """Latest AIMessage tool_calls not yet closed by a ToolMessage."""
    fulfilled: set[str] = set()
    for message in messages:
        if isinstance(message, ToolMessage) and message.tool_call_id:
            fulfilled.add(str(message.tool_call_id))
    pending: list[tuple[str, str, dict[str, Any]]] = []
    for message in reversed(messages):
        if not isinstance(message, AIMessage):
            continue
        for call in message.tool_calls or []:
            if not isinstance(call, dict):
                continue
            call_id = call.get("id")
            name = call.get("name")
            args = call.get("args")
            if (
                isinstance(call_id, str)
                and call_id
                and isinstance(name, str)
                and name
                and call_id not in fulfilled
            ):
                pending.append(
                    (
                        call_id,
                        name,
                        dict(args) if isinstance(args, dict) else {},
                    )
                )
        break
    return pending


class ToolCallRegistry:
    """Explicit tool-call lifecycle for one astream_events pass.

    Identity resolution (closed world):
    1. Event has call_id + name → use them; must match registry if already registered
    2. Missing call_id, has name → bind FIFO to the earliest open entry with that
       name (and required phase). LangGraph often omits tool_call_id on on_tool_start
       when the model emits parallel same-name calls; order matches register_from_model_step.
       Zero matches → protocol error
    3. Has call_id, missing name → lookup by id
    4. Both missing → exactly one STARTED entry
    """

    def __init__(self) -> None:
        self._entries: dict[str, ToolCallEntry] = {}

    def clear(self) -> None:
        self._entries.clear()

    def seed_from_messages(self, messages: list[BaseMessage]) -> None:
        self.clear()
        for call_id, tool_name, args in unresolved_pending_tool_calls(messages):
            self._entries[call_id] = ToolCallEntry(
                call_id=call_id,
                tool_name=tool_name,
                phase=ToolCallPhase.PENDING,
                args=args,
            )

    def register_from_model_step(self, tool_calls: list[dict[str, Any]]) -> None:
        self.clear()
        for call in tool_calls:
            call_id = call["id"]
            tool_name = call["name"]
            args = call.get("args")
            self._entries[call_id] = ToolCallEntry(
                call_id=call_id,
                tool_name=tool_name,
                phase=ToolCallPhase.PENDING,
                args=dict(args) if isinstance(args, dict) else {},
            )

    def open_entries(self) -> list[ToolCallEntry]:
        return list(self._entries.values())

    def resolve_identity(
        self,
        *,
        call_id: str,
        tool_name: str,
        require_phase: ToolCallPhase | None = None,
        context: str,
    ) -> tuple[str, str]:
        resolved_id = call_id
        resolved_name = tool_name

        if resolved_id and resolved_name:
            entry = self._entries.get(resolved_id)
            if entry is not None:
                if entry.tool_name != resolved_name:
                    raise GatewayChatError(
                        "gateway_protocol_error",
                        f"{context} call_id/name mismatch registry",
                        retryable=False,
                    )
                if require_phase is not None and entry.phase != require_phase:
                    raise GatewayChatError(
                        "gateway_protocol_error",
                        f"{context} unexpected phase={entry.phase.value}",
                        retryable=False,
                    )
            return resolved_id, resolved_name

        if resolved_id and not resolved_name:
            entry = self._entries.get(resolved_id)
            if entry is None:
                raise GatewayChatError(
                    "gateway_protocol_error",
                    f"{context} unknown call_id",
                    retryable=False,
                )
            if require_phase is not None and entry.phase != require_phase:
                raise GatewayChatError(
                    "gateway_protocol_error",
                    f"{context} unexpected phase={entry.phase.value}",
                    retryable=False,
                )
            return entry.call_id, entry.tool_name

        if resolved_name and not resolved_id:
            matches = [
                entry
                for entry in self._entries.values()
                if entry.tool_name == resolved_name
                and (require_phase is None or entry.phase == require_phase)
            ]
            if not matches:
                raise GatewayChatError(
                    "gateway_protocol_error",
                    f"{context} missing name={resolved_name} matches=0",
                    retryable=False,
                )
            # FIFO among same-name opens; dict insertion order == model tool_calls order.
            return matches[0].call_id, matches[0].tool_name

        started = [
            entry
            for entry in self._entries.values()
            if entry.phase == ToolCallPhase.STARTED
        ]
        if len(started) != 1:
            raise GatewayChatError(
                "gateway_protocol_error",
                f"{context} missing identity and started_count={len(started)}",
                retryable=False,
            )
        return started[0].call_id, started[0].tool_name

    def on_start(
        self,
        *,
        call_id: str,
        tool_name: str,
        args: dict[str, Any],
    ) -> ToolCallEntry:
        if not isinstance(args, dict):
            raise GatewayChatError(
                "gateway_protocol_error",
                "tool start event violates contract",
                retryable=False,
            )
        # Full id+name may arrive before register (rare); allow create. Name-only
        # binds FIFO to the earliest PENDING entry with that name.
        if call_id and tool_name:
            resolved_id, resolved_name = call_id, tool_name
            entry = self._entries.get(resolved_id)
            if entry is not None and entry.tool_name != resolved_name:
                raise GatewayChatError(
                    "gateway_protocol_error",
                    "tool start event call_id/name mismatch registry",
                    retryable=False,
                )
            if entry is not None and entry.phase == ToolCallPhase.STARTED:
                raise GatewayChatError(
                    "gateway_protocol_error",
                    "tool start event for already started call",
                    retryable=False,
                )
            if entry is None:
                entry = ToolCallEntry(
                    call_id=resolved_id,
                    tool_name=resolved_name,
                    phase=ToolCallPhase.PENDING,
                    args={},
                )
                self._entries[resolved_id] = entry
        else:
            resolved_id, resolved_name = self.resolve_identity(
                call_id=call_id,
                tool_name=tool_name,
                require_phase=ToolCallPhase.PENDING,
                context="tool start event",
            )
            entry = self._entries[resolved_id]
        entry.tool_name = resolved_name
        entry.args = dict(args)
        entry.phase = ToolCallPhase.STARTED
        return entry

    def on_end(self, *, call_id: str, tool_name: str) -> ToolCallEntry:
        return self._complete(
            call_id=call_id,
            tool_name=tool_name,
            context="tool end event",
        )

    def on_error(self, *, call_id: str, tool_name: str) -> ToolCallEntry:
        return self._complete(
            call_id=call_id,
            tool_name=tool_name,
            context="tool error event",
        )

    def _complete(
        self,
        *,
        call_id: str,
        tool_name: str,
        context: str,
    ) -> ToolCallEntry:
        resolved_id, resolved_name = self.resolve_identity(
            call_id=call_id,
            tool_name=tool_name,
            require_phase=ToolCallPhase.STARTED,
            context=context,
        )
        entry = self._entries.pop(resolved_id, None)
        if entry is None or entry.phase != ToolCallPhase.STARTED:
            raise GatewayChatError(
                "gateway_protocol_error",
                f"{context} has no matching start",
                retryable=False,
            )
        if tool_name and entry.tool_name != resolved_name:
            raise GatewayChatError(
                "gateway_protocol_error",
                f"{context} call_id/name mismatch registry",
                retryable=False,
            )
        return entry
