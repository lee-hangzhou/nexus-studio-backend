from __future__ import annotations

from dataclasses import dataclass

from tortoise.transactions import in_transaction

from app.contracts.gateway import GatewayResultItem
from app.domain.enums import GatewayTaskStatus
from app.exceptions.base import AppError
from app.exceptions.codes import ErrorCode
from app.models.generate_task import GenerateTask
from app.services.generation_assets import ensure_result_assets
from app.services.generation_canvas_bridge import publish_canvas_for_task, sync_canvas_for_task


TERMINAL_GENERATION_STATUSES = frozenset(
    {
        GatewayTaskStatus.SUCCEEDED,
        GatewayTaskStatus.FAILED,
        GatewayTaskStatus.CANCELLED,
    }
)


@dataclass(frozen=True)
class GenerationResult:
    status: GatewayTaskStatus
    result_keys: list[dict] | None
    error_message: str | None

    def update_fields(self, *, callback_sent: bool) -> dict:
        fields: dict = {"status": int(self.status), "callback_sent": callback_sent}
        if self.result_keys is not None:
            fields["result_keys"] = self.result_keys
        if self.error_message is not None:
            fields["error_message"] = self.error_message
        return fields


def normalize_generation_result(
    status: int | GatewayTaskStatus,
    urls: list[GatewayResultItem] | None,
    reason: str | None,
) -> GenerationResult:
    normalized_status = GatewayTaskStatus(status)
    result_keys = None
    error_message = reason
    if normalized_status == GatewayTaskStatus.SUCCEEDED:
        if not urls:
            normalized_status = GatewayTaskStatus.FAILED
            error_message = "成功终态缺少生成结果"
        else:
            result_keys = [
                item.model_dump(mode="json", exclude_none=False)
                for item in urls
            ]
    elif normalized_status == GatewayTaskStatus.FAILED:
        if not reason:
            raise AppError(ErrorCode.GATEWAY_PROTOCOL_ERROR, "失败终态缺少错误原因")
    elif urls:
        raise AppError(ErrorCode.GATEWAY_PROTOCOL_ERROR, "非成功终态不能携带生成结果")
    return GenerationResult(
        status=normalized_status,
        result_keys=result_keys,
        error_message=error_message,
    )


async def apply_generation_result(
    task: GenerateTask,
    result: GenerationResult,
    *,
    source: str,
    callback_sent: bool,
) -> tuple[GenerateTask, bool]:
    current = GatewayTaskStatus(task.status)
    if current in TERMINAL_GENERATION_STATUSES:
        return task, False
    if result.status not in TERMINAL_GENERATION_STATUSES and result.status < current:
        return task, False

    applied_task: GenerateTask
    canvas_patch = None
    async with in_transaction():
        updated = await GenerateTask.filter(
            id=task.id,
            status=int(current),
            deleted_at__isnull=True,
        ).update(**result.update_fields(callback_sent=callback_sent))
        if updated != 1:
            return await GenerateTask.get(id=task.id), False
        applied_task = await GenerateTask.get(id=task.id)
        if result.status == GatewayTaskStatus.SUCCEEDED:
            applied_task = await ensure_result_assets(applied_task)
        canvas_patch = await sync_canvas_for_task(applied_task, source=source, publish=False)
    await publish_canvas_for_task(applied_task, canvas_patch)
    return applied_task, True
