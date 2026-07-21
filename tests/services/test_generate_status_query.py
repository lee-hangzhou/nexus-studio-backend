from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.contracts.gateway import GatewayQueueResponse
from app.domain.enums import GatewayTaskStatus
from app.domain.generation.enums import GenerationTaskStatus
from app.services.generate_task import GenerateTaskService
from app.services.generate_task_views import GenerateTaskViewAssembler


def _task(
    *,
    task_id: int,
    gateway_task_id: int,
    status: GatewayTaskStatus,
    ref_attachment_ids: list[int] | None = None,
    result_asset_ids: list[int] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=task_id,
        union_task_id=gateway_task_id,
        kind="image",
        status=status,
        prompt=f"prompt-{task_id}",
        model_id="image-model",
        ratio="1:1",
        resolution="2k",
        duration=None,
        reference_mode=None,
        ref_attachment_ids=ref_attachment_ids,
        ref_asset_ids=None,
        result_keys=None,
        result_asset_ids=result_asset_ids or [],
        error_message=None,
        created_at=datetime(2026, 7, 21, tzinfo=timezone.utc),
    )


@pytest.mark.asyncio
async def test_status_query_uses_local_state_and_assembles_read_data() -> None:
    succeeded = _task(
        task_id=1,
        gateway_task_id=101,
        status=GatewayTaskStatus.SUCCEEDED,
        result_asset_ids=[11],
    )
    queued = _task(
        task_id=2,
        gateway_task_id=202,
        status=GatewayTaskStatus.QUEUED,
        ref_attachment_ids=[12],
    )
    attachment = SimpleNamespace(
        id=12,
        asset_id=None,
        filename="reference.png",
        mime_type="image/png",
        storage_key="references/reference.png",
    )
    queue_response = GatewayQueueResponse.model_validate(
        {
            "code": 0,
            "message": "ok",
            "data": {
                "tasks": [
                    {
                        "taskId": 202,
                        "status": int(GatewayTaskStatus.RUNNING),
                        "position": 3,
                        "total": 8,
                        "estimatedWaitSeconds": 90,
                    }
                ]
            },
        }
    )

    task_repository = MagicMock()
    task_repository.get_by_ids_for_user = AsyncMock(
        return_value=[succeeded, queued]
    )
    attachment_repository = MagicMock()
    attachment_repository.get_by_ids_for_user = AsyncMock(
        return_value=[attachment]
    )
    asset_repository = MagicMock()
    asset_repository.get_active_by_ids_for_user = AsyncMock(return_value=[])
    asset_repository.get_favorited_by_ids_for_user = AsyncMock(
        return_value=[SimpleNamespace(id=11)]
    )
    gateway_client = MagicMock()
    gateway_client.get_tasks_queue = AsyncMock(return_value=queue_response)
    attachment_service = MagicMock()
    attachment_service.build_preview_url.return_value = "/preview/reference.png"

    service = GenerateTaskService(
        task_repository=task_repository,
        asset_repository=asset_repository,
        attachment_repository=attachment_repository,
        gateway_client=gateway_client,
        attachment_service=attachment_service,
        view_assembler=GenerateTaskViewAssembler(
            asset_service=MagicMock(),
            attachment_service=attachment_service,
        ),
    )

    result = await service.get_tasks_status([2, 1, 999, 2], user_id=7)

    assert {item.task_id for item in result.items} == {1, 2}
    assert result.missing_task_ids == [999]
    queued_view = next(item for item in result.items if item.task_id == 2)
    succeeded_view = next(item for item in result.items if item.task_id == 1)
    assert queued_view.status == GenerationTaskStatus.QUEUED
    assert queued_view.queue_status == GatewayTaskStatus.RUNNING
    assert queued_view.queue_position == 3
    assert queued_view.estimated_wait_seconds == 90
    assert queued_view.ref_materials[0].attachment_id == 12
    assert queued_view.ref_materials[0].url == "/preview/reference.png"
    assert succeeded_view.is_favorited is True
