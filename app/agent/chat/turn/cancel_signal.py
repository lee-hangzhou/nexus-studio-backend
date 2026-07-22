"""Cross-request turn cancel signal (Redis), for /turn/cancel while SSE agent loop runs."""

from __future__ import annotations

from app.server.infra.config import settings
from app.server.infra.redis import redis_client


class TurnCancelSignal:
    def _key(self, conversation_id: int) -> str:
        return f"chat:turn_cancel:{conversation_id}"

    async def signal(self, conversation_id: int, turn_id: str) -> None:
        await redis_client.set(
            self._key(conversation_id),
            turn_id,
            ex=settings.CHAT_TURN_LOCK_TTL_SEC,
        )

    async def peek(self, conversation_id: int) -> str | None:
        return await redis_client.get(self._key(conversation_id))

    async def matches(self, conversation_id: int, turn_id: str) -> bool:
        active = await self.peek(conversation_id)
        return active == turn_id

    async def clear(self, conversation_id: int, turn_id: str) -> None:
        key = self._key(conversation_id)
        if await redis_client.get(key) == turn_id:
            await redis_client.delete(key)


turn_cancel_signal = TurnCancelSignal()
