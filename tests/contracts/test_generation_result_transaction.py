import pytest
from tortoise import Tortoise
from unittest.mock import MagicMock

from app.contracts.gateway import GatewayResultItem
from app.server.generation.domain.gateway_status import GatewayTaskStatus
from app.server.generation.persistence.generate_task import GenerateTask
from app.server.generation.persistence.repository import GenerateTaskRepository
from app.server.generation.domain.terminal import normalize_gateway_terminal
from app.server.generation.services.service import GenerationService


def _service() -> GenerationService:
    return GenerationService(
        task_repository=GenerateTaskRepository(),
        asset_repository=MagicMock(),
        gateway_client=MagicMock(),
        asset_service=MagicMock(),
        object_storage=MagicMock(),
        model_cache=MagicMock(),
    )


@pytest.mark.asyncio
async def test_generation_result_asset_failure_rolls_back_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await Tortoise.init(
        db_url="sqlite://:memory:",
        modules={"models": ["app.server.generation.persistence.generate_task"]},
    )
    await Tortoise.generate_schemas()
    try:
        task = await GenerateTask.create(
            user_id=1,
            union_task_id=7,
            kind="image",
            status=GatewayTaskStatus.RUNNING,
            prompt="test",
            model_id="test-model",
        )
        service = _service()

        async def fail_assets(current: GenerateTask) -> GenerateTask:
            raise RuntimeError("asset write failed")

        monkeypatch.setattr(service, "ensure_result_assets", fail_assets)
        result = normalize_gateway_terminal(
            GatewayTaskStatus.SUCCEEDED,
            [GatewayResultItem(url="result/key", type=2, width=1, height=1)],
            None,
        )

        with pytest.raises(RuntimeError):
            await service.apply_result(task, result, callback_sent=True)

        stored = await GenerateTask.get(id=task.id)
        assert stored.status == GatewayTaskStatus.RUNNING
        assert stored.result_keys is None
        assert stored.callback_sent is False
    finally:
        await Tortoise.close_connections()
