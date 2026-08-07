from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.contracts.gateway import GatewayQueueResponse, GatewayTaskStatusResponse
from app.server.api.v1.endpoints import generate as generate_endpoint
from app.server.generation.domain.enums import GenerationKind, GenerationTaskStatus
from app.server.generation.domain.gateway_status import GatewayTaskStatus
from app.server.generation.domain.terminal import GenerationTerminal
from app.server.generation.schemas import (
    GenerateCallbackPayload,
    GenerateTasksStatusRequest,
    GenerateTasksStatusResponse,
    GenerateTaskStatusRequest,
    GenerateTaskView,
)
from app.server.generation.schemas.callback import GenerationCallbackResult
from app.server.generation.services import GenerationService


def _task(
    *,
    task_id: int,
    gateway_task_id: int | None,
    status: GatewayTaskStatus,
    ref_asset_ids: list[int] | None = None,
    result_asset_ids: list[int] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=task_id,
        user_id=7,
        union_task_id=gateway_task_id,
        kind="image",
        status=status,
        prompt=f"prompt-{task_id}",
        model_id="image-model",
        ratio="1:1",
        resolution="2k",
        duration=None,
        reference_mode=None,
        ref_asset_ids=ref_asset_ids,
        result_keys=None,
        result_asset_ids=result_asset_ids or [],
        error_message=None,
        created_at=datetime(2026, 7, 21, tzinfo=timezone.utc),
    )


def _service(
    *,
    tasks: list[SimpleNamespace],
    gateway_client: MagicMock,
    assets: list[SimpleNamespace] | None = None,
    favorited: list[SimpleNamespace] | None = None,
) -> GenerationService:
    task_repository = MagicMock()
    task_repository.get_by_ids_for_user = AsyncMock(return_value=tasks)
    asset_repository = MagicMock()
    asset_repository.get_active_by_ids_for_user = AsyncMock(return_value=assets or [])
    asset_repository.get_favorited_by_ids_for_user = AsyncMock(
        return_value=favorited or []
    )
    return GenerationService(
        task_repository=task_repository,
        asset_repository=asset_repository,
        gateway_client=gateway_client,
        asset_service=MagicMock(),
        object_storage=MagicMock(),
        model_cache=MagicMock(),
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
        ref_asset_ids=[12],
    )
    asset = SimpleNamespace(
        id=12,
        filename="reference.png",
        mime_type="image/png",
        storage_key="references/reference.png",
        source_type="manual_upload",
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

    async def fake_apply(self, task, result, *, callback_sent):
        assert callback_sent is False
        assert result.status == GatewayTaskStatus.RUNNING
        task.status = result.status
        return task, True

    monkeypatch.setattr(
        "app.server.generation.services.service.GenerationService.apply_result",
        fake_apply,
    )
    asset_service = MagicMock()
    asset_service.preview_url.return_value = "/preview/reference.png"
    service = _service(
        tasks=[queued],
        gateway_client=gateway_client,
        assets=[asset],
    )
    service._asset_service = asset_service

    result = await service.get_tasks_status([2, 999, 2], user_id=7)

    assert result.missing_task_ids == [999]
    view = result.items[0]
    assert view.task_id == 2
    assert view.status == GenerationTaskStatus.RUNNING
    assert "queue_status" not in view.model_dump()
    assert view.queue_position == 3
    assert view.queue_total == 8
    assert view.estimated_wait_seconds == 90
    assert len(view.ref_materials) == 1
    assert view.ref_materials[0].asset_id == 12
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
        "app.server.generation.services.service.GenerationService.apply_result",
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

    async def fake_apply(self, task, result: GenerationTerminal, *, callback_sent):
        assert callback_sent is True
        assert result.status == GatewayTaskStatus.SUCCEEDED
        assert result.result_keys is not None
        task.status = result.status
        task.result_keys = result.result_keys
        return task, True

    monkeypatch.setattr(
        "app.server.generation.services.service.GenerationService.apply_result",
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
    outcome = GenerationCallbackResult(task=task, applied=True)
    handle = AsyncMock(return_value=outcome)
    project = AsyncMock(return_value=None)
    monkeypatch.setattr(generate_endpoint, "generation_service", SimpleNamespace(handle_callback=handle))
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
    project.assert_awaited_once_with(9, 7)


def _task_view(
    *,
    task_id: int,
    status: GatewayTaskStatus,
) -> GenerateTaskView:
    return GenerateTaskView(
        task_id=task_id,
        kind=GenerationKind.IMAGE,
        status=GenerationTaskStatus(status),
        prompt=f"prompt-{task_id}",
        model_id="image-model",
        created_at=datetime(2026, 7, 21, tzinfo=timezone.utc),
    )


def _status_request() -> MagicMock:
    request = MagicMock()
    request.state.user_id = 7
    return request


@pytest.mark.asyncio
async def test_tasks_status_endpoint_does_not_project_canvas(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = GenerateTasksStatusResponse(
        items=[
            _task_view(task_id=9, status=GatewayTaskStatus.SUCCEEDED),
            _task_view(task_id=10, status=GatewayTaskStatus.RUNNING),
        ],
        missing_task_ids=[999],
    )
    get_tasks_status = AsyncMock(return_value=response)
    project = AsyncMock(return_value=None)
    monkeypatch.setattr(
        generate_endpoint,
        "generation_service",
        SimpleNamespace(get_tasks_status=get_tasks_status),
    )
    monkeypatch.setattr(generate_endpoint, "project_from_task", project)

    result = await generate_endpoint.get_tasks_status(
        _status_request(),
        GenerateTasksStatusRequest(task_ids=[9, 10, 999]),
    )

    assert result.data.items == response.items
    get_tasks_status.assert_awaited_once_with([9, 10, 999], 7)
    project.assert_not_called()


@pytest.mark.asyncio
async def test_task_status_endpoint_does_not_project_canvas(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    get_task_status = AsyncMock(
        return_value=_task_view(task_id=9, status=GatewayTaskStatus.FAILED)
    )
    project = AsyncMock(return_value=None)
    monkeypatch.setattr(
        generate_endpoint,
        "generation_service",
        SimpleNamespace(get_task_status=get_task_status),
    )
    monkeypatch.setattr(generate_endpoint, "project_from_task", project)

    await generate_endpoint.get_task_status(
        _status_request(),
        GenerateTaskStatusRequest(task_id=9),
    )

    project.assert_not_called()
