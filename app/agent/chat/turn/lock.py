from __future__ import annotations

import asyncio
from dataclasses import dataclass

from app.server.exceptions.base import AppError
from app.server.exceptions.codes import ErrorCode
from app.server.infra.config import settings
from app.server.infra.logger import logger
from app.server.infra.redis import redis_client


@dataclass(frozen=True, slots=True)
class ActiveConversationTurn:
    turn_id: str
    cancel_event: asyncio.Event
    completed_event: asyncio.Event


class ConversationTurnLock:
    _RELEASE_POLL_INTERVAL_SEC = 0.05

    _REFRESH_SCRIPT = """
if redis.call('GET', KEYS[1]) ~= ARGV[1] then
  return 0
end
return redis.call('EXPIRE', KEYS[1], ARGV[2])
"""

    _RELEASE_SCRIPT = """
if redis.call('GET', KEYS[1]) ~= ARGV[1] then
  return 0
end
return redis.call('DEL', KEYS[1])
"""

    def __init__(self) -> None:
        self._active_turns: dict[int, ActiveConversationTurn] = {}

    def _key(self, conversation_id: int) -> str:
        return f"chat:turn_lock:{conversation_id}"

    async def acquire(
        self,
        conversation_id: int,
        turn_id: str,
        *,
        cancel_event: asyncio.Event | None = None,
    ) -> ActiveConversationTurn | None:
        ok = await redis_client.set(
            self._key(conversation_id),
            turn_id,
            ex=settings.CHAT_TURN_LOCK_TTL_SEC,
            nx=True,
        )
        if not ok:
            active = await redis_client.get(self._key(conversation_id))
            raise AppError(
                ErrorCode.CONVERSATION_BUSY,
                f"conversation {conversation_id} is busy",
                details={"active_turn_id": active},
            )
        if cancel_event is None:
            return None
        active_turn = ActiveConversationTurn(
            turn_id=turn_id,
            cancel_event=cancel_event,
            completed_event=asyncio.Event(),
        )
        self._active_turns[conversation_id] = active_turn
        return active_turn

    async def refresh(self, conversation_id: int, turn_id: str) -> bool:
        result = await redis_client.client.eval(  # type: ignore[misc]
            self._REFRESH_SCRIPT,
            1,
            self._key(conversation_id),
            turn_id,
            str(settings.CHAT_TURN_LOCK_TTL_SEC),
        )
        return bool(result)

    async def release(self, conversation_id: int, turn_id: str) -> bool:
        result = await redis_client.client.eval(  # type: ignore[misc]
            self._RELEASE_SCRIPT,
            1,
            self._key(conversation_id),
            turn_id,
        )
        released = bool(result)
        local_active = self._active_turns.get(conversation_id)
        if local_active is not None and local_active.turn_id == turn_id:
            del self._active_turns[conversation_id]
            local_active.completed_event.set()
        return released

    async def peek(self, conversation_id: int) -> str | None:
        return await redis_client.get(self._key(conversation_id))

    async def force_cancel(self, conversation_id: int) -> str | None:
        key = self._key(conversation_id)
        active = await redis_client.get(key)
        if active:
            await redis_client.delete(key)
        return active

    async def cancel_and_wait(self, conversation_id: int, *, timeout_sec: float) -> str | None:
        active_turn_id = await self.peek(conversation_id)
        if active_turn_id is None:
            return None

        local_active = self._active_turns.get(conversation_id)
        if local_active is not None and local_active.turn_id == active_turn_id:
            local_active.cancel_event.set()
            logger.info(
                "chat.turn.cancel",
                action="requested",
                conversation_id=conversation_id,
                active_turn_id=active_turn_id,
            )
            wait_target = local_active.completed_event.wait()
        else:
            wait_target = self._wait_until_released(conversation_id, active_turn_id)

        try:
            await asyncio.wait_for(wait_target, timeout=timeout_sec)
        except TimeoutError as exc:
            raise AppError(
                ErrorCode.TASK_CANCEL_FAILED,
                "chat turn cancellation cleanup timed out",
                details={"active_turn_id": active_turn_id},
            ) from exc
        return active_turn_id

    async def _wait_until_released(self, conversation_id: int, turn_id: str) -> None:
        while await self.peek(conversation_id) == turn_id:
            await asyncio.sleep(self._RELEASE_POLL_INTERVAL_SEC)


conversation_turn_lock = ConversationTurnLock()
