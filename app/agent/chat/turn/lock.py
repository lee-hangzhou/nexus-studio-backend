from app.server.infra.config import settings
from app.server.infra.redis import redis_client
from app.server.exceptions.base import AppError
from app.server.exceptions.codes import ErrorCode


class ConversationTurnLock:
    def _key(self, conversation_id: int) -> str:
        return f"chat:turn_lock:{conversation_id}"

    async def acquire(self, conversation_id: int, turn_id: str) -> None:
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

    async def release(self, conversation_id: int, turn_id: str) -> None:
        key = self._key(conversation_id)
        active = await redis_client.get(key)
        if active == turn_id:
            await redis_client.delete(key)

    async def peek(self, conversation_id: int) -> str | None:
        return await redis_client.get(self._key(conversation_id))

    async def force_cancel(self, conversation_id: int) -> str | None:
        key = self._key(conversation_id)
        active = await redis_client.get(key)
        if active:
            await redis_client.delete(key)
        return active


conversation_turn_lock = ConversationTurnLock()
