"""Clear orphan turn locks after process restart or crash."""

from __future__ import annotations

from app.chat.turn.cancel_signal import turn_cancel_signal
from app.chat.turn.lock import conversation_turn_lock
from app.core.logger import logger
from app.models.chat_conversations import ChatConversations


async def clear_stale_active_turns_on_startup() -> int:
    """进程重启后 DB active_turn_id 与 Redis turn lock 均不可信，统一清理。"""
    rows = await ChatConversations.filter(active_turn_id__not_isnull=True).all()
    if not rows:
        return 0

    cleared = 0
    for row in rows:
        active_turn_id = row.active_turn_id
        await conversation_turn_lock.force_cancel(row.id)
        if active_turn_id:
            await turn_cancel_signal.signal(row.id, active_turn_id)
        row.active_turn_id = None
        row.active_turn_started_at = None
        await row.save(update_fields=["active_turn_id", "active_turn_started_at", "updated_at"])
        cleared += 1
        logger.warning(
            "chat.turn.stale_cleared",
            conversation_id=row.id,
            turn_id=active_turn_id,
        )
    logger.info("chat.turn.stale_cleanup.done", cleared_count=cleared)
    return cleared
