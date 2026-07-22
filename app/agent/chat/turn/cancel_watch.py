"""Poll Redis cancel signal and set in-process cancel_event."""

from __future__ import annotations

import asyncio

from app.agent.chat.turn.cancel_signal import turn_cancel_signal
from app.server.infra.config import settings
from app.server.infra.logger import logger

_POLL_SEC = min(1.0, max(0.2, settings.CHAT_HEARTBEAT_INTERVAL_SEC / 2))


async def watch_turn_cancel(
    *,
    conversation_id: int,
    turn_id: str,
    cancel_event: asyncio.Event,
) -> None:
    try:
        while not cancel_event.is_set():
            if await turn_cancel_signal.matches(conversation_id, turn_id):
                logger.info(
                    "chat.turn.cancel_signal_received",
                    conversation_id=conversation_id,
                    turn_id=turn_id,
                )
                cancel_event.set()
                return
            await asyncio.sleep(_POLL_SEC)
    except asyncio.CancelledError:
        return
