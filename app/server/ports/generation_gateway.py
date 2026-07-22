from typing import Protocol

from app.contracts.gateway import (
    GatewayImageSubmitRequest,
    GatewayListVoicesResponse,
    GatewayModelItem,
    GatewayQueueResponse,
    GatewayTaskStatusResponse,
    GatewayTaskSubmitResponse,
    GatewayTTSSubmitRequest,
    GatewayVideoSubmitRequest,
)


class GenerationGatewayPort(Protocol):
    async def list_generation_models(self) -> list[GatewayModelItem]: ...

    async def submit_image(
        self,
        payload: GatewayImageSubmitRequest,
        request_id: str,
    ) -> GatewayTaskSubmitResponse: ...

    async def submit_video(
        self,
        payload: GatewayVideoSubmitRequest,
        request_id: str,
    ) -> GatewayTaskSubmitResponse: ...

    async def submit_tts(
        self,
        payload: GatewayTTSSubmitRequest,
        request_id: str,
    ) -> GatewayTaskSubmitResponse: ...

    async def list_voices(
        self,
        model: str,
        request_id: str | None = None,
    ) -> GatewayListVoicesResponse: ...

    async def get_task(self, task_id: int) -> GatewayTaskStatusResponse: ...

    async def get_tasks_queue(self, task_ids: list[int]) -> GatewayQueueResponse: ...

    async def cancel_task(self, task_id: int, user_id: str) -> None: ...
