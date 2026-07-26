from __future__ import annotations

import asyncio
from dataclasses import dataclass

from app.server.canvas.domain.constants import CANVAS_TURN_LOCK_KEY_TEMPLATE
from app.server.exceptions.base import AppError
from app.server.exceptions.codes import ErrorCode
from app.server.infra.config import settings
from app.server.infra.logger import logger
from app.server.infra.redis import redis_client


@dataclass(frozen=True, slots=True)
class ActiveCanvasTurn:
    turn_id: str
    cancel_event: asyncio.Event
    completed_event: asyncio.Event


class CanvasTurnLock:
    """基于 Redis NX 的集级 turn 互斥锁，并登记本进程取消协调"""

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
        self._active_turns: dict[int, ActiveCanvasTurn] = {}

    def _key(self, episode_id: int) -> str:
        return CANVAS_TURN_LOCK_KEY_TEMPLATE.format(episode_id=episode_id)

    async def acquire(
        self,
        episode_id: int,
        turn_id: str,
        *,
        cancel_event: asyncio.Event | None = None,
    ) -> ActiveCanvasTurn | None:
        ok = await redis_client.set(
            self._key(episode_id),
            turn_id,
            ex=settings.CANVAS_TURN_LOCK_TTL_SEC,
            nx=True,
        )
        if not ok:
            active = await redis_client.get(self._key(episode_id))
            logger.info(
                "canvas.turn.lock",
                action="busy",
                episode_id=episode_id,
                turn_id=turn_id,
                active_turn_id=active,
            )
            raise AppError(
                ErrorCode.CANVAS_EPISODE_BUSY,
                f"canvas episode {episode_id} is busy",
                details={"active_turn_id": active},
            )
        logger.info("canvas.turn.lock", action="acquire", episode_id=episode_id, turn_id=turn_id)
        if cancel_event is None:
            return None
        active_turn = ActiveCanvasTurn(
            turn_id=turn_id,
            cancel_event=cancel_event,
            completed_event=asyncio.Event(),
        )
        self._active_turns[episode_id] = active_turn
        return active_turn

    async def refresh(self, episode_id: int, turn_id: str) -> bool:
        result = await redis_client.client.eval(  # type: ignore[misc]
            self._REFRESH_SCRIPT,
            1,
            self._key(episode_id),
            turn_id,
            str(settings.CANVAS_TURN_LOCK_TTL_SEC),
        )
        return bool(result)

    async def release(self, episode_id: int, turn_id: str) -> bool:
        result = await redis_client.client.eval(  # type: ignore[misc]
            self._RELEASE_SCRIPT,
            1,
            self._key(episode_id),
            turn_id,
        )
        released = bool(result)
        local_active = self._active_turns.get(episode_id)
        if local_active is not None and local_active.turn_id == turn_id:
            del self._active_turns[episode_id]
            local_active.completed_event.set()
        if released:
            logger.info("canvas.turn.lock", action="release", episode_id=episode_id, turn_id=turn_id)
        return released

    async def active_turn(self, episode_id: int) -> str | None:
        return await redis_client.get(self._key(episode_id))

    async def force_cancel(self, episode_id: int) -> str | None:
        key = self._key(episode_id)
        active = await redis_client.get(key)
        if active:
            await redis_client.delete(key)
        return active

    async def cancel_and_wait(self, episode_id: int, *, timeout_sec: float) -> str | None:
        active_turn_id = await self.active_turn(episode_id)
        if active_turn_id is None:
            return None

        local_active = self._active_turns.get(episode_id)
        if local_active is not None and local_active.turn_id == active_turn_id:
            local_active.cancel_event.set()
            logger.info(
                "canvas.turn.cancel",
                action="requested",
                episode_id=episode_id,
                active_turn_id=active_turn_id,
            )
            wait_target = local_active.completed_event.wait()
        else:
            logger.info(
                "canvas.turn.cancel",
                action="wait_remote_release",
                episode_id=episode_id,
                active_turn_id=active_turn_id,
            )
            wait_target = self._wait_until_released(episode_id, active_turn_id)

        try:
            await asyncio.wait_for(wait_target, timeout=timeout_sec)
        except TimeoutError as exc:
            logger.warning(
                "canvas.turn.cancel",
                action="cleanup_timeout",
                episode_id=episode_id,
                active_turn_id=active_turn_id,
                timeout_sec=timeout_sec,
            )
            raise AppError(
                ErrorCode.TASK_CANCEL_FAILED,
                "canvas turn cancellation cleanup timed out",
                details={"active_turn_id": active_turn_id},
            ) from exc

        logger.info(
            "canvas.turn.cancel",
            action="completed",
            episode_id=episode_id,
            active_turn_id=active_turn_id,
        )
        return active_turn_id

    async def _wait_until_released(self, episode_id: int, turn_id: str) -> None:
        while await self.active_turn(episode_id) == turn_id:
            await asyncio.sleep(self._RELEASE_POLL_INTERVAL_SEC)


canvas_turn_lock = CanvasTurnLock()
