from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.api.v1.endpoints import generate as generate_endpoint
from app.contracts.gateway import GatewayQueueResponse, GatewayTaskStatusResponse
from app.domain.enums import GatewayTaskStatus
from app.domain.generation.enums import GenerationTaskStatus
from app.schemas.generate import GenerateCallbackPayload
from app.services.generate_task import GenerateTaskService, GenerationCallbackOutcome
from app.services.generate_task_views import GenerateTaskViewAssembler
from app.services.generation_result import GenerationResult


def _task(
    *,
    task_id: int,
    gateway_task_id: int | None,
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


def _service(
    *,
    tasks: list[SimpleNamespace],
    gateway_client: MagicMock,
    attachments: list[SimpleNamespace] | None = None,
    favorited: list[SimpleNamespace] | None = None,
) -> GenerateTaskService:
    task_repository = MagicMock()
    task_repository.get_by_ids_for_user = AsyncMock(return_value=tasks)
    attachment_repository = MagicMock()
    attachment_repository.get_by_ids_for_user = AsyncMock(
        return_value=attachments or []
    )
    asset_repository = MagicMock()
    asset_repository.get_active_by_ids_for_user = AsyncMock(return_value=[])
    asset_repository.get_favorited_by_ids_for_user = AsyncMock(
        return_value=favorited or []
    )
    attachment_service = MagicMock()
    attachment_service.build_preview_url.return_value = "/preview/reference.png"
    return GenerateTaskService(
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


@pytest.mark.asyncio
async def test_status_observe_skips_gateway_for_terminal_tasks() -> None:
    succeeded = _task(
        task_id=1,
        gateway_task_id=101,
        status=GatewayTaskStatus.SUCCEEDED,
        result_asset_ids=[11],
    )
    gateway_client = MagicMock()
    gateway_client.get_tasks_queue = AsyncMock()
    gateway_client.get_task = AsyncMock()
    service = _service(
        tasks=[succeeded],
        gateway_client=gateway_client,
        favorited=[SimpleNamespace(id=11)],
    )

    result = await service.get_tasks_status([1], user_id=7)

    assert result.items[0].status == GenerationTaskStatus.SUCCEEDED
    assert result.items[0].is_favorited is True
    gateway_client.get_tasks_queue.assert_not_called()
    gateway_client.get_task.assert_not_called()


@pytest.mark.asyncio
async def test_status_observe_cas_updates_when_gateway_status_differs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    gateway_client = MagicMock()
    gateway_client.get_tasks_queue = AsyncMock(return_value=queue_response)
    gateway_client.get_task = AsyncMock()

    async def fake_apply(task, result, *, callback_sent):
        assert callback_sent is False
        assert result.status == GatewayTaskStatus.RUNNING
        task.status = result.status
        return task, True

    monkeypatch.setattr(
        "app.services.generate_task.apply_generation_result",
        fake_apply,
    )
    service = _service(
        tasks=[queued],
        gateway_client=gateway_client,
        attachments=[attachment],
    )

    result = await service.get_tasks_status([2, 999, 2], user_id=7)

    assert result.missing_task_ids == [999]
    view = result.items[0]
    assert view.task_id == 2
    assert view.status == GenerationTaskStatus.RUNNING
    assert "queue_status" not in view.model_dump()
    assert view.queue_position == 3
    assert view.queue_total == 8
    assert view.estimated_wait_seconds == 90
    assert view.ref_materials[0].attachment_id == 12
    gateway_client.get_task.assert_not_called()


@pytest.mark.asyncio
async def test_status_observe_does_not_write_when_gateway_matches_local(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    running = _task(
        task_id=3,
        gateway_task_id=303,
        status=GatewayTaskStatus.RUNNING,
    )
    queue_response = GatewayQueueResponse.model_validate(
        {
            "code": 0,
            "message": "ok",
            "data": {
                "tasks": [
                    {
                        "taskId": 303,
                        "status": int(GatewayTaskStatus.RUNNING),
                        "position": 1,
                        "total": 2,
                        "estimatedWaitSeconds": 20,
                    }
                ]
            },
        }
    )
    gateway_client = MagicMock()
    gateway_client.get_tasks_queue = AsyncMock(return_value=queue_response)
    apply_mock = AsyncMock()
    monkeypatch.setattr(
        "app.services.generate_task.apply_generation_result",
        apply_mock,
    )
    service = _service(tasks=[running], gateway_client=gateway_client)

    result = await service.get_tasks_status([3], user_id=7)

    assert result.items[0].status == GenerationTaskStatus.RUNNING
    assert result.items[0].estimated_wait_seconds == 20
    apply_mock.assert_not_called()


@pytest.mark.asyncio
async def test_status_observe_terminal_fetches_task_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queued = _task(
        task_id=4,
        gateway_task_id=404,
        status=GatewayTaskStatus.QUEUED,
    )
    queue_response = GatewayQueueResponse.model_validate(
        {
            "code": 0,
            "message": "ok",
            "data": {
                "tasks": [
                    {
                        "taskId": 404,
                        "status": int(GatewayTaskStatus.SUCCEEDED),
                        "position": 0,
                        "total": 0,
                        "estimatedWaitSeconds": 0,
                    }
                ]
            },
        }
    )
    task_response = GatewayTaskStatusResponse.model_validate(
        {
            "code": 0,
            "message": "ok",
            "data": {
                "taskId": 404,
                "status": int(GatewayTaskStatus.SUCCEEDED),
                "urls": [{"url": "https://cdn.example/a.png", "type": 1}],
                "reason": None,
            },
        }
    )
    gateway_client = MagicMock()
    gateway_client.get_tasks_queue = AsyncMock(return_value=queue_response)
    gateway_client.get_task = AsyncMock(return_value=task_response)

    async def fake_apply(task, result: GenerationResult, *, callback_sent):
        assert callback_sent is True
        assert result.status == GatewayTaskStatus.SUCCEEDED
        assert result.result_keys is not None
        task.status = result.status
        task.result_keys = result.result_keys
        return task, True

    monkeypatch.setattr(
        "app.services.generate_task.apply_generation_result",
        fake_apply,
    )
    service = _service(tasks=[queued], gateway_client=gateway_client)

    result = await service.get_tasks_status([4], user_id=7)

    assert result.items[0].status == GenerationTaskStatus.SUCCEEDED
    assert result.items[0].estimated_wait_seconds == 0
    gateway_client.get_task.assert_awaited_once_with(404)


@pytest.mark.asyncio
async def test_callback_endpoint_projects_canvas_after_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = _task(task_id=9, gateway_task_id=909, status=GatewayTaskStatus.SUCCEEDED)
    outcome = GenerationCallbackOutcome(task=task, applied=True)
    handle = AsyncMock(return_value=outcome)
    project = AsyncMock(return_value=None)
    monkeypatch.setattr(generate_endpoint, "generate_task_service", SimpleNamespace(handle_callback=handle))
    monkeypatch.setattr(generate_endpoint, "project_from_task", project)

    payload = GenerateCallbackPayload.model_validate(
        {
            "taskId": 909,
            "status": int(GatewayTaskStatus.SUCCEEDED),
            "urls": [{"url": "https://cdn.example/a.png", "type": 1}],
        }
    )
    await generate_endpoint.receive_generate_callback(payload)

    handle.assert_awaited_once_with(payload)
    project.assert_awaited_once_with(task)
