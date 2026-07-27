from __future__ import annotations

import asyncio
from dataclasses import dataclass

from app.server.canvas.domain.constants import CANVAS_SESSION_TURN_LOCK_KEY_TEMPLATE
from app.server.canvas.services.episode_fence import canvas_episode_fence
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


class CanvasSessionTurnLock:
    """基于 Redis NX 的 session 级 turn 互斥锁, 并登记本进程取消协调"""

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
        """初始化本进程 active turn 表"""
        self._active_turns: dict[int, ActiveCanvasTurn] = {}

    def _key(self, session_id: int) -> str:
        """拼 session turn 锁 Redis key"""
        return CANVAS_SESSION_TURN_LOCK_KEY_TEMPLATE.format(session_id=session_id)

    async def acquire(
        self,
        session_id: int,
        turn_id: str,
        *,
        cancel_event: asyncio.Event | None = None,
    ) -> ActiveCanvasTurn | None:
        """抢占 session turn 锁, 忙则抛 CANVAS_SESSION_BUSY"""
        ok = await redis_client.set(
            self._key(session_id),
            turn_id,
            ex=settings.CANVAS_TURN_LOCK_TTL_SEC,
            nx=True,
        )
        if not ok:
            active = await redis_client.get(self._key(session_id))
            logger.info(
                "canvas.session.turn.lock",
                action="busy",
                session_id=session_id,
                turn_id=turn_id,
                active_turn_id=active,
            )
            raise AppError(
                ErrorCode.CANVAS_SESSION_BUSY,
                f"canvas session {session_id} is busy",
                details={"active_turn_id": active, "session_id": session_id},
            )
        logger.info(
            "canvas.session.turn.lock",
            action="acquire",
            session_id=session_id,
            turn_id=turn_id,
        )
        if cancel_event is None:
            return None
        active_turn = ActiveCanvasTurn(
            turn_id=turn_id,
            cancel_event=cancel_event,
            completed_event=asyncio.Event(),
        )
        self._active_turns[session_id] = active_turn
        return active_turn

    async def refresh(self, session_id: int, turn_id: str) -> bool:
        """续期 session turn 锁 TTL"""
        result = await redis_client.client.eval(  # type: ignore[misc]
            self._REFRESH_SCRIPT,
            1,
            self._key(session_id),
            turn_id,
            str(settings.CANVAS_TURN_LOCK_TTL_SEC),
        )
        return bool(result)

    async def release(self, session_id: int, turn_id: str) -> bool:
        """释放本 turn 持有的 session 锁"""
        result = await redis_client.client.eval(  # type: ignore[misc]
            self._RELEASE_SCRIPT,
            1,
            self._key(session_id),
            turn_id,
        )
        released = bool(result)
        local_active = self._active_turns.get(session_id)
        if local_active is not None and local_active.turn_id == turn_id:
            del self._active_turns[session_id]
            local_active.completed_event.set()
        if released:
            logger.info(
                "canvas.session.turn.lock",
                action="release",
                session_id=session_id,
                turn_id=turn_id,
            )
        return released

    async def active_turn(self, session_id: int) -> str | None:
        """读当前持锁 turn_id"""
        return await redis_client.get(self._key(session_id))

    async def force_release(self, session_id: int) -> str | None:
        """强制删除锁并唤醒本进程等待方"""
        key = self._key(session_id)
        active = await redis_client.get(key)
        if active:
            await redis_client.delete(key)
            local_active = self._active_turns.get(session_id)
            if local_active is not None and local_active.turn_id == active:
                del self._active_turns[session_id]
                local_active.completed_event.set()
        return active

    async def cancel_and_wait(self, session_id: int, *, timeout_sec: float) -> str | None:
        """请求取消并等待锁释放, 超时强制回收后抛错"""
        active_turn_id = await self.active_turn(session_id)
        if active_turn_id is None:
            return None

        local_active = self._active_turns.get(session_id)
        if local_active is not None and local_active.turn_id == active_turn_id:
            local_active.cancel_event.set()
            logger.info(
                "canvas.session.turn.cancel",
                action="requested",
                session_id=session_id,
                active_turn_id=active_turn_id,
            )
            wait_target = local_active.completed_event.wait()
        else:
            logger.info(
                "canvas.session.turn.cancel",
                action="wait_remote_release",
                session_id=session_id,
                active_turn_id=active_turn_id,
            )
            wait_target = self._wait_until_released(session_id, active_turn_id)

        try:
            await asyncio.wait_for(wait_target, timeout=timeout_sec)
        except TimeoutError as exc:
            logger.warning(
                "canvas.session.turn.cancel",
                action="cleanup_timeout",
                session_id=session_id,
                active_turn_id=active_turn_id,
                timeout_sec=timeout_sec,
            )
            await self.force_release(session_id)
            raise AppError(
                ErrorCode.TASK_CANCEL_FAILED,
                "canvas session turn cancellation cleanup timed out",
                details={"active_turn_id": active_turn_id, "session_id": session_id},
            ) from exc

        logger.info(
            "canvas.session.turn.cancel",
            action="completed",
            session_id=session_id,
            active_turn_id=active_turn_id,
        )
        return active_turn_id

    async def _wait_until_released(self, session_id: int, turn_id: str) -> None:
        """轮询直到远程锁不再属于该 turn"""
        while await self.active_turn(session_id) == turn_id:
            await asyncio.sleep(self._RELEASE_POLL_INTERVAL_SEC)


canvas_turn_lock = CanvasSessionTurnLock()
# 兼容旧名: 集级删集栅栏已迁到 server.canvas.services.episode_fence
canvas_episode_mutex = canvas_episode_fence
