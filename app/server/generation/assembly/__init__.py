from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.contracts.gateway import GatewayResultItem
from app.contracts.generation import GenerateParamOptions, ReferenceModeOption
from app.server.assets.persistence.assets import Assets
from app.server.assets.services.service import ASSET_SOURCE_GENERATE_RESULT
from app.server.generation.domain.constants import (
    REF_SOURCE_CHAT_ATTACHMENT,
    REFERENCE_MODE_LABELS,
    RESULT_ASSET_META_TASK_ID,
)
from app.server.generation.domain.models import GenerationModelCapabilities
from app.server.generation.persistence.generate_task import GenerateTask
from app.server.generation.schemas import (
    GenerateRefMaterial,
    GenerateTaskListItem,
    GenerateTaskView,
)
from app.server.generation.schemas.observation import GatewayQueueObservation
from app.server.infra.logger import logger


def task_result_asset_ids(task: GenerateTask) -> list[int]:
    # result_asset_ids 来自 JSON 列，持久化边界做一次整型归一
    if not isinstance(task.result_asset_ids, list):
        return []
    return [int(item) for item in task.result_asset_ids if item is not None]


def asset_task_id(row: Assets) -> int | None:
    # metadata / source_id 来自 JSON 与字符串列
    metadata = row.metadata if isinstance(row.metadata, dict) else {}
    raw = metadata.get(RESULT_ASSET_META_TASK_ID)
    if raw is None and row.source_type == ASSET_SOURCE_GENERATE_RESULT:
        raw = row.source_id
    try:
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def collect_reference_ids(tasks: list[GenerateTask]) -> tuple[set[int], set[int]]:
    attachment_ids: set[int] = set()
    asset_ids: set[int] = set()
    for task in tasks:
        attachment_ids.update(task.ref_attachment_ids or [])
        asset_ids.update(task.ref_asset_ids or [])
    return attachment_ids, asset_ids


def to_param_options(capabilities: GenerationModelCapabilities) -> GenerateParamOptions:
    return GenerateParamOptions(
        ratios=list(capabilities.ratios),
        resolutions=list(capabilities.resolutions),
        counts=list(capabilities.counts),
        durations=list(capabilities.durations),
        reference_modes=[
            ReferenceModeOption(value=mode, label=REFERENCE_MODE_LABELS[mode])
            for mode in capabilities.reference_modes
        ],
        ratios_by_resolution={
            resolution: list(ratios) for resolution, ratios in capabilities.ratios_by_resolution
        },
    )


def to_result_urls(
    result_keys: list[dict[str, Any]],
    *,
    presign: Callable[[str], str],
) -> list[GatewayResultItem]:
    result: list[GatewayResultItem] = []
    for raw_item in result_keys:
        item = GatewayResultItem.model_validate(raw_item)
        try:
            result.append(item.model_copy(update={"url": presign(item.url)}))
        except Exception as exc:
            logger.warning("generate.presign_error", key=item.url, error=str(exc))
            result.append(item)
    return result


def to_task_view(
    task: GenerateTask,
    *,
    observation: GatewayQueueObservation | None,
    ref_materials: list[GenerateRefMaterial],
    favorited_asset_ids: set[int],
    presign: Callable[[str], str],
) -> GenerateTaskView:
    result_urls = to_result_urls(task.result_keys, presign=presign) if task.result_keys else []
    result_asset_ids = task_result_asset_ids(task)
    view = GenerateTaskView(
        task_id=task.id,
        kind=task.kind,
        status=task.status,
        prompt=task.prompt,
        model_id=task.model_id,
        ratio=task.ratio,
        resolution=task.resolution,
        duration=task.duration,
        reference_mode=task.reference_mode,
        ref_materials=ref_materials,
        result_count=len(result_urls),
        result_urls=result_urls,
        result_asset_ids=result_asset_ids,
        error_message=task.error_message,
        is_favorited=bool(favorited_asset_ids.intersection(result_asset_ids)),
        created_at=task.created_at,
    )
    if observation is not None:
        view.queue_position = observation.position
        view.queue_total = observation.total
        view.estimated_wait_seconds = observation.estimated_wait_seconds
    return view


def to_task_list_item(
    task: GenerateTask,
    *,
    observation: GatewayQueueObservation | None,
    favorited_asset_ids: set[int],
    presign: Callable[[str], str],
) -> GenerateTaskListItem:
    result_urls = to_result_urls(task.result_keys, presign=presign) if task.result_keys else []
    result_asset_ids = task_result_asset_ids(task)
    preview = result_urls[0] if result_urls else None
    view = GenerateTaskListItem(
        task_id=task.id,
        kind=task.kind,
        status=task.status,
        prompt=task.prompt,
        model_id=task.model_id,
        ratio=task.ratio,
        resolution=task.resolution,
        duration=task.duration,
        reference_mode=task.reference_mode,
        result_count=len(result_urls),
        preview_url=preview.url if preview is not None else None,
        preview_asset_id=result_asset_ids[0] if result_asset_ids else None,
        preview_media_type=preview.type if preview is not None else None,
        error_message=task.error_message,
        is_favorited=bool(favorited_asset_ids.intersection(result_asset_ids)),
        created_at=task.created_at,
    )
    if observation is not None:
        view.queue_position = observation.position
        view.queue_total = observation.total
        view.estimated_wait_seconds = observation.estimated_wait_seconds
    return view


def ref_material_from_attachment(
    *,
    attachment_id: int,
    asset_id: int | None,
    filename: str,
    mime_type: str,
    url: str,
) -> GenerateRefMaterial:
    return GenerateRefMaterial(
        attachment_id=attachment_id,
        asset_id=asset_id,
        filename=filename,
        mime_type=mime_type,
        url=url,
        source_type=REF_SOURCE_CHAT_ATTACHMENT,
    )


def ref_material_from_asset(
    *,
    asset_id: int,
    filename: str,
    mime_type: str,
    url: str,
    source_type: str,
) -> GenerateRefMaterial:
    return GenerateRefMaterial(
        asset_id=asset_id,
        filename=filename,
        mime_type=mime_type,
        url=url,
        source_type=source_type,
    )
