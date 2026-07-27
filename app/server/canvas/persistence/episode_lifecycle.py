from __future__ import annotations

from dataclasses import dataclass

from app.server.canvas.domain.enums import CanvasNodeStatus
from app.server.canvas.persistence.edges import CanvasEdges
from app.server.canvas.persistence.episode_meta import CanvasEpisodeMeta
from app.server.canvas.persistence.messages import CanvasMessages
from app.server.canvas.persistence.nodes import CanvasNodes
from app.server.canvas.persistence.operations import CanvasOperations
from app.server.canvas.persistence.sessions import CanvasSessions


@dataclass(frozen=True, slots=True)
class CanvasEpisodeDeleteState:
    running_node_count: int
    task_ids: tuple[int, ...]


class CanvasEpisodeLifecycleRepository:
    async def create_empty(self, episode_id: int) -> None:
        await CanvasEpisodeMeta.create(episode_id=episode_id)

    async def get_delete_state(self, episode_id: int) -> CanvasEpisodeDeleteState:
        # Include soft-deleted nodes: DeleteNodeOp can leave RUNNING + in-flight tasks
        # that would otherwise be missed and orphaned by delete_all.
        rows = await CanvasNodes.filter(episode_id=episode_id).only("status", "task_id")
        return CanvasEpisodeDeleteState(
            running_node_count=sum(1 for row in rows if row.status == CanvasNodeStatus.RUNNING.value),
            task_ids=tuple(
                dict.fromkeys(int(row.task_id) for row in rows if row.task_id is not None)
            ),
        )

    async def delete_all(self, episode_id: int) -> None:
        await CanvasOperations.filter(episode_id=episode_id).delete()
        await CanvasMessages.filter(episode_id=episode_id).delete()
        await CanvasSessions.filter(episode_id=episode_id).delete()
        await CanvasEdges.filter(episode_id=episode_id).delete()
        await CanvasNodes.filter(episode_id=episode_id).delete()
        await CanvasEpisodeMeta.filter(episode_id=episode_id).delete()
