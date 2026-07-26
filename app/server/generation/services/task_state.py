from __future__ import annotations

from collections.abc import Collection

from app.server.generation.domain.gateway_status import GatewayTaskStatus
from app.server.generation.persistence.repository import GenerateTaskRepository


class GenerationTaskStateService:
    def __init__(self, repository: GenerateTaskRepository) -> None:
        self._tasks = repository

    async def has_non_terminal(self, task_ids: Collection[int]) -> bool:
        tasks = await self._tasks.get_active_by_ids(task_ids)
        return any(GatewayTaskStatus(task.status).is_non_terminal for task in tasks)
