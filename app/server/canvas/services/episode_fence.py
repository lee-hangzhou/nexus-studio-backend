from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import uuid4

from app.server.canvas.domain.constants import (
    CANVAS_EPISODE_GEN_INFLIGHT_KEY_TEMPLATE,
    CANVAS_EPISODE_MUTEX_KEY_TEMPLATE,
)
from app.server.exceptions.base import AppError
from app.server.exceptions.codes import ErrorCode
from app.server.infra.config import settings
from app.server.infra.logger import logger
from app.server.infra.redis import redis_client


class CanvasEpisodeFence:
    """集级写栅栏: exclusive mutex + 在途生成登记, 供删集等待排空"""

    _RELEASE_SCRIPT = """
if redis.call('GET', KEYS[1]) ~= ARGV[1] then
  return 0
end
return redis.call('DEL', KEYS[1])
"""
    _POLL_INTERVAL_SEC = 0.05

    def _mutex_key(self, episode_id: int) -> str:
        """拼删集 exclusive mutex key"""
        return CANVAS_EPISODE_MUTEX_KEY_TEMPLATE.format(episode_id=episode_id)

    def _gen_key(self, episode_id: int) -> str:
        """拼在途生成登记 set key"""
        return CANVAS_EPISODE_GEN_INFLIGHT_KEY_TEMPLATE.format(episode_id=episode_id)

    async def assert_writable(self, episode_id: int) -> None:
        """exclusive 持锁窗口内拒绝写路径"""
        active = await redis_client.get(self._mutex_key(episode_id))
        if active is not None:
            raise AppError(
                ErrorCode.CANVAS_EPISODE_BUSY,
                f"canvas episode {episode_id} is busy",
                details={"active_owner": active},
            )

    async def assert_available(self, episode_id: int) -> None:
        """兼容旧名: 同 assert_writable"""
        await self.assert_writable(episode_id)

    async def acquire_exclusive(self, episode_id: int, owner: str) -> None:
        """抢占删集 exclusive mutex"""
        ok = await redis_client.set(
            self._mutex_key(episode_id),
            owner,
            ex=settings.CANVAS_TURN_LOCK_TTL_SEC,
            nx=True,
        )
        if not ok:
            active = await redis_client.get(self._mutex_key(episode_id))
            raise AppError(
                ErrorCode.CANVAS_EPISODE_BUSY,
                f"canvas episode {episode_id} is busy",
                details={"active_owner": active},
            )
        logger.info("canvas.episode.mutex", action="acquire", episode_id=episode_id, owner=owner)

    async def acquire(self, episode_id: int, owner: str) -> None:
        """兼容旧名: 同 acquire_exclusive"""
        await self.acquire_exclusive(episode_id, owner)

    async def release_exclusive(self, episode_id: int, owner: str) -> bool:
        """释放删集 exclusive mutex"""
        result = await redis_client.client.eval(  # type: ignore[misc]
            self._RELEASE_SCRIPT,
            1,
            self._mutex_key(episode_id),
            owner,
        )
        released = bool(result)
        if released:
            logger.info("canvas.episode.mutex", action="release", episode_id=episode_id, owner=owner)
        return released

    async def release(self, episode_id: int, owner: str) -> bool:
        """兼容旧名: 同 release_exclusive"""
        return await self.release_exclusive(episode_id, owner)

    async def enter_generation(self, episode_id: int, owner: str) -> None:
        """登记在途生成; 若删集已持锁则失败"""
        await self.assert_writable(episode_id)
        key = self._gen_key(episode_id)
        await redis_client.sadd(key, owner)
        await redis_client.expire(key, int(settings.CANVAS_TURN_LOCK_TTL_SEC))
        try:
            await self.assert_writable(episode_id)
        except AppError:
            await redis_client.srem(key, owner)
            raise
        logger.info(
            "canvas.episode.generation",
            action="enter",
            episode_id=episode_id,
            owner=owner,
        )

    async def leave_generation(self, episode_id: int, owner: str) -> None:
        """注销在途生成"""
        await redis_client.srem(self._gen_key(episode_id), owner)
        logger.info(
            "canvas.episode.generation",
            action="leave",
            episode_id=episode_id,
            owner=owner,
        )

    @asynccontextmanager
    async def generation(self, episode_id: int, *, owner: str | None = None) -> AsyncIterator[str]:
        """包住生成临界区, 供删集 wait_generations_idle"""
        token = owner or f"gen:{uuid4().hex}"
        await self.enter_generation(episode_id, token)
        try:
            yield token
        finally:
            await self.leave_generation(episode_id, token)

    async def wait_generations_idle(self, episode_id: int, *, timeout_sec: float) -> None:
        """等到在途生成登记清空; 超时抛错, 不强制清登记"""
        key = self._gen_key(episode_id)
        deadline = asyncio.get_running_loop().time() + timeout_sec
        while True:
            members = await redis_client.smembers(key)
            count = len(members)
            if count == 0:
                return
            if asyncio.get_running_loop().time() >= deadline:
                logger.warning(
                    "canvas.episode.generation",
                    action="wait_timeout",
                    episode_id=episode_id,
                    inflight=count,
                    owners=sorted(members)[:20],
                    timeout_sec=timeout_sec,
                )
                raise AppError(
                    ErrorCode.CANVAS_EPISODE_BUSY,
                    "canvas episode generation still in flight",
                    details={"inflight": count, "episode_id": episode_id},
                )
            await asyncio.sleep(self._POLL_INTERVAL_SEC)


canvas_episode_fence = CanvasEpisodeFence()
