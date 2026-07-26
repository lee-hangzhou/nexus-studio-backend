from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from typing import Any

from app.server.infra.logger import logger


class BackgroundSupervisor:
    """跟踪命名后台任务；可选按 key 在单进程内串行"""

    def __init__(self) -> None:
        self._tasks: set[asyncio.Task[Any]] = set()
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock_for(self, key: str) -> asyncio.Lock:
        """返回（并缓存）串行锁"""
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        return lock

    def start(
        self,
        coroutine: Awaitable[None],
        *,
        name: str | None = None,
        serial_key: str | None = None,
    ) -> None:
        """调度后台协程；提供 serial_key 时同 key FIFO"""

        async def runner() -> None:
            """可选持锁后执行业务协程"""
            if serial_key is None:
                await coroutine
                return
            async with self._lock_for(serial_key):
                await coroutine

        task = asyncio.create_task(runner(), name=name)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        task.add_done_callback(self._log_failure)

    async def close(self) -> None:
        """取消并排空全部受管任务"""
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()

    @staticmethod
    def _log_failure(task: asyncio.Task[Any]) -> None:
        """记录非预期后台失败"""
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.error(
                "background.task_failed",
                name=task.get_name(),
                error=str(exc),
            )


# 进程共享监督器（replay + memory extract）
background_supervisor = BackgroundSupervisor()
