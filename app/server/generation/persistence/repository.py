from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from tortoise.expressions import Q

from app.server.generation.domain.enums import GenerationKind, GenerationTaskStatus
from app.server.generation.domain.gateway_status import TERMINAL_GATEWAY_TASK_STATUSES, GatewayTaskStatus
from app.server.generation.persistence.generate_task import GenerateTask
from app.server.persistence.repository_base import BaseRepository


@dataclass(frozen=True, slots=True)
class GenerateTaskQuery:
    user_id: int
    limit: int
    kind: GenerationKind | None = None
    statuses: tuple[GenerationTaskStatus, ...] = ()
    created_after: datetime | None = None
    prompt_query: str = ""
    task_ids: frozenset[int] | None = None
    cursor_created_at: datetime | None = None
    cursor_task_id: int | None = None


class GenerateTaskRepository(BaseRepository[GenerateTask]):
    def __init__(self) -> None:
        self.model = GenerateTask

    async def get_by_ids_for_user(
        self,
        task_ids: Collection[int],
        user_id: int,
    ) -> list[GenerateTask]:
        if not task_ids:
            return []
        return await self.model.filter(
            id__in=task_ids,
            user_id=user_id,
            deleted_at__isnull=True,
        ).all()

    async def get_active_for_user(self, task_id: int, user_id: int) -> GenerateTask | None:
        return await self.model.get_or_none(
            id=task_id,
            user_id=user_id,
            deleted_at__isnull=True,
        )

    async def get_by_union_task_id(self, union_task_id: int) -> GenerateTask | None:
        return await self.model.get_or_none(union_task_id=union_task_id)

    async def get_by_id_required(self, task_id: int) -> GenerateTask:
        return await self.model.get(id=task_id)

    async def list_by_query(self, query: GenerateTaskQuery) -> list[GenerateTask]:
        if query.task_ids is not None and not query.task_ids:
            return []

        rows = self.model.filter(
            user_id=query.user_id,
            deleted_at__isnull=True,
        )
        if query.kind is not None:
            rows = rows.filter(kind=query.kind)
        if query.statuses:
            rows = rows.filter(status__in=query.statuses)
        if query.created_after is not None:
            rows = rows.filter(created_at__gte=query.created_after)
        if query.prompt_query:
            rows = rows.filter(prompt__icontains=query.prompt_query)
        if query.task_ids is not None:
            rows = rows.filter(id__in=query.task_ids)
        if query.cursor_created_at is not None and query.cursor_task_id is not None:
            rows = rows.filter(
                Q(created_at__lt=query.cursor_created_at)
                | Q(
                    created_at=query.cursor_created_at,
                    id__lt=query.cursor_task_id,
                )
            )

        return await rows.order_by("-created_at", "-id").limit(query.limit)

    async def mark_failed_if_non_terminal(self, task_id: int, error_message: str) -> None:
        await self.model.filter(
            id=task_id,
            status__not_in=[int(status) for status in TERMINAL_GATEWAY_TASK_STATUSES],
        ).update(status=GatewayTaskStatus.FAILED, error_message=error_message)

    async def bind_union_task_queued(self, task_id: int, union_task_id: int) -> bool:
        updated = await self.model.filter(
            id=task_id,
            status=GatewayTaskStatus.CREATED,
        ).update(
            union_task_id=union_task_id,
            status=GatewayTaskStatus.QUEUED,
        )
        if updated == 1:
            return True
        await self.model.filter(id=task_id).update(union_task_id=union_task_id)
        return False

    async def cancel_if_non_terminal(self, task_id: int) -> bool:
        updated = await self.model.filter(
            id=task_id,
            status__not_in=[int(status) for status in TERMINAL_GATEWAY_TASK_STATUSES],
        ).update(status=GatewayTaskStatus.CANCELLED)
        return updated == 1

    async def soft_delete(self, task_id: int) -> None:
        await self.model.filter(id=task_id, deleted_at__isnull=True).update(
            deleted_at=datetime.now(timezone.utc),
        )

    async def mark_callback_sent(self, task_id: int) -> None:
        await self.model.filter(id=task_id, deleted_at__isnull=True).update(callback_sent=True)

    async def set_result_asset_ids(self, task_id: int, asset_ids: list[int]) -> GenerateTask:
        await self.model.filter(id=task_id).update(result_asset_ids=asset_ids)
        return await self.get_by_id_required(task_id)

    async def update_if_status(
        self,
        task_id: int,
        *,
        expected_status: GatewayTaskStatus,
        fields: Mapping[str, Any],
    ) -> int:
        return await self.model.filter(
            id=task_id,
            status=int(expected_status),
            deleted_at__isnull=True,
        ).update(**fields)
