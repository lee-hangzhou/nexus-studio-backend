from __future__ import annotations

from app.canvas.services.generation_sync import (
    reconcile_canvas_node_for_task,
    sync_canvas_node_from_generate_task,
)
from app.contracts.canvas import CanvasPatchResponse
from app.core.logger import logger
from app.models.generate_task import GenerateTask


async def reconcile_canvas_for_task(
    task: GenerateTask,
    *,
    source: str,
    ensure_assets: bool = True,
) -> CanvasPatchResponse | None:
    try:
        return await reconcile_canvas_node_for_task(task, ensure_assets=ensure_assets)
    except Exception as exc:
        logger.exception(
            "canvas.generation_sync.failed",
            task_id=task.id,
            source=source,
            error=str(exc),
        )
        return None


async def sync_canvas_for_task(
    task: GenerateTask,
    *,
    source: str,
) -> CanvasPatchResponse | None:
    try:
        return await sync_canvas_node_from_generate_task(task)
    except Exception as exc:
        logger.exception(
            "canvas.generation_sync.failed",
            task_id=task.id,
            source=source,
            error=str(exc),
        )
        return None
