from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from app.contracts.gateway import GatewayQueueItem
from app.domain.enums import GatewayTaskStatus


@dataclass(frozen=True)
class GatewayQueueObservation:
    union_task_id: int
    status: GatewayTaskStatus
    position: int | None
    total: int | None
    estimated_wait_seconds: int | None

    @classmethod
    def from_queue_item(cls, item: GatewayQueueItem) -> GatewayQueueObservation:
        return cls(
            union_task_id=item.task_id,
            status=GatewayTaskStatus(item.status),
            position=item.position,
            total=item.total,
            estimated_wait_seconds=item.estimated_wait_seconds,
        )


@dataclass(frozen=True)
class GatewayObservationBatch:
    _by_union_task_id: Mapping[int, GatewayQueueObservation]

    @classmethod
    def empty(cls) -> GatewayObservationBatch:
        return cls(_by_union_task_id={})

    @classmethod
    def from_queue_items(cls, items: list[GatewayQueueItem]) -> GatewayObservationBatch:
        return cls(
            _by_union_task_id={
                item.task_id: GatewayQueueObservation.from_queue_item(item)
                for item in items
            }
        )

    def for_union_task(self, union_task_id: int) -> GatewayQueueObservation | None:
        return self._by_union_task_id.get(union_task_id)
