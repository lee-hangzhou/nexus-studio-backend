from __future__ import annotations

from app.canvas.services.generation_sync import (
    reconcile_canvas_node_for_task,
    publish_canvas_node_result,
    sync_canvas_node_from_generate_task,
)
from app.contracts.canvas import CanvasPatchResponse
from app.models.generate_task import GenerateTask


async def reconcile_canvas_for_task(
    task: GenerateTask,
    *,
    source: str,
    ensure_assets: bool = True,
) -> CanvasPatchResponse | None:
    _ = source
    return await reconcile_canvas_node_for_task(task, ensure_assets=ensure_assets)


async def sync_canvas_for_task(
    task: GenerateTask,
    *,
    source: str,
    publish: bool = True,
) -> CanvasPatchResponse | None:
    _ = source
    return await sync_canvas_node_from_generate_task(task, publish=publish)


async def publish_canvas_for_task(
    task: GenerateTask,
    payload: CanvasPatchResponse | None,
) -> None:
    await publish_canvas_node_result(task, payload)
