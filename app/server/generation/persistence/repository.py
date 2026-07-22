from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime

from tortoise.expressions import Q

from app.server.generation.domain.enums import GenerationKind, GenerationTaskStatus
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

    async def list_by_query(
        self,
        query: GenerateTaskQuery,
    ) -> list[GenerateTask]:
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
