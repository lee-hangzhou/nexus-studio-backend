"""SSE terminal-frame policy (single owner: SseTurnSubscriber)."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class SseTerminalPolicy:
    """Who may emit DONE after which terminal; message_ids for chat DONE."""

    emit_done_on_completed: bool = True
    # Chat legacy contract: ERROR/CANCELLED paths still close with DONE.
    emit_done_after_failure: bool = False
    message_ids: Callable[[], Sequence[int]] | None = None
