from __future__ import annotations

import asyncio
from typing import Any


class CanvasGenerationHub:
    """按 project_id 管理生成进度 SSE 订阅队列"""

    def __init__(self) -> None:
        """初始化进程内订阅表"""
        self._queues: dict[int, set[asyncio.Queue[dict[str, Any] | None]]] = {}

    def subscribe(self, project_id: int) -> asyncio.Queue[dict[str, Any] | None]:
        """订阅某项目的生成进度事件"""
        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        self._queues.setdefault(project_id, set()).add(queue)
        return queue

    def unsubscribe(self, project_id: int, queue: asyncio.Queue[dict[str, Any] | None]) -> None:
        """取消订阅, 避免连接关闭后队列泄漏"""
        subs = self._queues.get(project_id)
        if subs is not None:
            subs.discard(queue)
            if not subs:
                self._queues.pop(project_id, None)

    async def publish(self, project_id: int, payload: dict[str, Any]) -> None:
        """向当前进程内全部订阅者广播进度"""
        for queue in list(self._queues.get(project_id, ())):
            await queue.put(payload)


canvas_generation_hub = CanvasGenerationHub()
