from typing import Protocol

from app.contracts.gateway import GatewayQueueResponse, GatewayTaskStatusResponse


class GenerationGatewayPort(Protocol):
    async def get_task(self, task_id: int) -> GatewayTaskStatusResponse: ...

    async def get_tasks_queue(self, task_ids: list[int]) -> GatewayQueueResponse: ...

    async def cancel_task(self, task_id: int, user_id: str) -> None: ...
