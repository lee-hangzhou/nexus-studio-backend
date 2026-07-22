import pytest
from tortoise import Tortoise

from app.contracts.gateway import GatewayResultItem
from app.domain.enums import GatewayTaskStatus
from app.models.generate_task import GenerateTask
from app.services import generation_result


@pytest.mark.asyncio
async def test_generation_result_asset_failure_rolls_back_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await Tortoise.init(
        db_url="sqlite://:memory:",
        modules={"models": ["app.models.generate_task"]},
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

        async def fail_assets(current: GenerateTask) -> GenerateTask:
            raise RuntimeError("asset write failed")

        monkeypatch.setattr(generation_result, "ensure_result_assets", fail_assets)
        result = generation_result.normalize_generation_result(
            GatewayTaskStatus.SUCCEEDED,
            [GatewayResultItem(url="result/key", type=2, width=1, height=1)],
            None,
        )

        with pytest.raises(RuntimeError):
            await generation_result.apply_generation_result(
                task,
                result,
                callback_sent=True,
            )

        stored = await GenerateTask.get(id=task.id)
        assert stored.status == GatewayTaskStatus.RUNNING
        assert stored.result_keys is None
        assert stored.callback_sent is False
    finally:
        await Tortoise.close_connections()
