from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal
from uuid import UUID, uuid4

from tortoise.exceptions import DoesNotExist
from tortoise.expressions import Q
from tortoise.transactions import in_transaction

from app.contracts.canvas import CanvasRevisionConflictItem
from app.server.assets.persistence.assets import Assets
from app.server.assets.services.service import ASSET_SOURCE_GENERATE_RESULT, asset_service
from app.server.canvas.domain.constants import (
    CANVAS_OPERATION_STATUS_APPLIED,
    CANVAS_OPERATION_TYPE_APPLY_PATCH,
)
from app.server.canvas.domain.enums import CanvasNodeStatus
from app.server.canvas.persistence.edges import CanvasEdges
from app.server.canvas.persistence.episode_meta import CanvasEpisodeMeta
from app.server.canvas.persistence.messages import CanvasMessages
from app.server.canvas.persistence.nodes import CanvasNodes
from app.server.canvas.persistence.operations import CanvasOperations
from app.server.canvas.services.episode_events import publish_canvas_patch
from app.server.canvas.schemas.api import (
    CanvasEdgeView,
    CanvasMessageView,
    CanvasNodeView,
    CanvasPatchOp,
    CanvasPatchResponse,
    CanvasPosition,
    CanvasSnapshot,
    ConnectNodesOp,
    CreateNodeOp,
    DeleteNodeOp,
    DisconnectNodesOp,
    UpdateNodeOp,
)
from app.server.exceptions.base import AppError
from app.server.exceptions.codes import ErrorCode
from app.server.projects.domain.models import CanvasScope
from app.server.projects.persistence.episodes import ProjectEpisodes


class CanvasRevisionConflictError(AppError):
    def __init__(self, conflicts: list[CanvasRevisionConflictItem]) -> None:
        super().__init__(
            ErrorCode.CANVAS_REVISION_CONFLICT,
            "canvas revision conflict",
            details={
                "error_type": "revision_conflict",
                "conflicts": [item.model_dump(mode="json") for item in conflicts],
            },
        )


def node_view_from_row(row: CanvasNodes) -> CanvasNodeView:
    """把节点 ORM 行转成契约视图"""
    output_asset_ids = row.output_asset_ids
    output_asset_list = [int(item) for item in output_asset_ids] if isinstance(output_asset_ids, list) else None
    return CanvasNodeView(
        id=row.id,
        kind=row.kind,
        revision=row.revision,
        position=CanvasPosition(x=row.position_x, y=row.position_y),
        title=row.title,
        input_prompt=row.input_prompt,
        output_text=row.output_text,
        status=row.status,
        model_id=row.model_id,
        voice_id=row.voice_id,
        ratio=row.ratio,
        duration_sec=row.duration_sec,
        resolution=row.resolution,
        task_id=row.task_id,
        output_asset_ids=output_asset_list,
        error_message=row.error_message,
    )


def _edge_view(row: CanvasEdges) -> CanvasEdgeView:
    """把边 ORM 行转成契约视图"""
    return CanvasEdgeView(
        id=row.id,
        revision=row.revision,
        source=row.source_node_id,
        target=row.target_node_id,
        source_port=row.source_port,
        target_port=row.target_port,
        edge_type=row.edge_type,
        metadata=row.metadata,
    )


def _metadata_int(metadata: dict | None, key: str) -> int | None:
    """从 metadata 字典读取可选整型字段"""
    if not isinstance(metadata, dict):
        return None
    value = metadata.get(key)
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _asset_allowed_for_node(scope: CanvasScope, node: CanvasNodeView, asset: Assets) -> bool:
    """判断资产是否允许出现在该节点预览 URL 列表中"""
    if int(asset.user_id) != scope.user_id:
        return False
    if asset.deleted_at is not None or asset.status != "ready":
        return False
    if asset.project_id is not None and int(asset.project_id) != scope.project_id:
        return False

    metadata = asset.metadata if isinstance(asset.metadata, dict) else {}
    episode_id = _metadata_int(metadata, "episode_id")
    if episode_id is not None:
        return episode_id == scope.episode_id

    if node.task_id is not None and asset.source_type == ASSET_SOURCE_GENERATE_RESULT:
        task_id = int(node.task_id)
        if asset.source_id is not None and str(asset.source_id) == str(task_id):
            return True
        if _metadata_int(metadata, "task_id") == task_id:
            return True
        if _metadata_int(metadata, "generation_task_id") == task_id:
            return True

    return asset.project_id is not None and int(asset.project_id) == scope.project_id


async def refresh_node_asset_urls(nodes: list[CanvasNodeView], *, scope: CanvasScope) -> None:
    """为节点视图填充可预览的 output_asset_urls"""
    asset_ids = {
        int(asset_id)
        for node in nodes
        for asset_id in (node.output_asset_ids or [])
    }
    assets = (
        await Assets.filter(
            id__in=list(asset_ids),
            user_id=scope.user_id,
            status="ready",
            deleted_at__isnull=True,
        ).all()
        if asset_ids
        else []
    )
    assets_by_id = {int(row.id): row for row in assets}
    for node in nodes:
        urls = [
            asset_service.preview_url(assets_by_id[asset_id].storage_key)
            for asset_id in (node.output_asset_ids or [])
            if asset_id in assets_by_id and _asset_allowed_for_node(scope, node, assets_by_id[asset_id])
        ]
        node.output_asset_urls = urls or None


class CanvasService:
    async def _require_meta(self, episode_id: int) -> CanvasEpisodeMeta:
        """读取 episode 画布元信息, 不存在则报错"""
        meta = await CanvasEpisodeMeta.filter(episode_id=episode_id).first()
        if meta is None:
            raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "canvas episode meta not found")
        return meta

    async def get_snapshot(self, scope: CanvasScope) -> CanvasSnapshot:
        """组装画布快照"""
        await self._require_meta(scope.episode_id)
        nodes = await CanvasNodes.filter(episode_id=scope.episode_id, deleted_at__isnull=True).all()
        edges = await CanvasEdges.filter(episode_id=scope.episode_id, deleted_at__isnull=True).all()
        node_views = [node_view_from_row(n) for n in nodes]
        await refresh_node_asset_urls(node_views, scope=scope)
        return CanvasSnapshot(
            project_id=scope.project_id,
            episode_id=scope.episode_id,
            nodes=node_views,
            edges=[_edge_view(e) for e in edges],
        )

    async def list_messages(
        self,
        scope: CanvasScope,
        *,
        session_id: int,
        before_id: int | None,
        limit: int,
    ) -> list[CanvasMessageView]:
        """按 session 游标倒序分页列出画布消息"""
        query = CanvasMessages.filter(
            episode_id=scope.episode_id,
            session_id=session_id,
        ).order_by("-created_at")
        if before_id is not None:
            query = query.filter(id__lt=before_id)
        rows = await query.limit(limit)
        views: list[CanvasMessageView] = []
        for row in reversed(rows):
            metadata = row.metadata or {}
            raw_input = metadata.get("input")
            views.append(
                CanvasMessageView(
                    id=row.id,
                    role=row.role,
                    content=row.content,
                    input=raw_input if isinstance(raw_input, dict) else None,
                    metadata=metadata,
                    created_at=row.created_at.isoformat() if row.created_at else "",
                )
            )
        return views

    async def refresh_node_asset_urls(self, nodes: list[CanvasNodeView], *, scope: CanvasScope) -> None:
        """服务入口包装 refresh_node_asset_urls"""
        await refresh_node_asset_urls(nodes, scope=scope)

    async def _require_project_output_asset_ids(
        self,
        scope: CanvasScope,
        output_asset_ids: list[int] | None,
    ) -> list[int] | None:
        """拒绝客户端直接写入 output_asset_ids"""
        if output_asset_ids is None:
            return None
        ids = [int(item) for item in output_asset_ids]
        if not ids:
            return []
        raise AppError(
            ErrorCode.INVALID_PARAMS,
            "output_asset_ids is managed by generation projection",
            details={"asset_ids": ids},
        )

    async def _attach_generation_output_assets(
        self,
        scope: CanvasScope,
        output_asset_ids: list[int] | None,
        *,
        node_id: str,
        task_id: int | None,
    ) -> list[int] | None:
        """校验并绑定生成结果资产到项目与节点 metadata"""
        if output_asset_ids is None:
            return None
        ids = [int(item) for item in output_asset_ids]
        if not ids:
            return []
        rows = await Assets.filter(
            id__in=list(dict.fromkeys(ids)),
            user_id=scope.user_id,
            status="ready",
            deleted_at__isnull=True,
        ).all()
        by_id = {int(row.id): row for row in rows}
        missing: list[int] = []
        for asset_id in dict.fromkeys(ids):
            row = by_id.get(asset_id)
            if row is None or (row.project_id is not None and int(row.project_id) != scope.project_id):
                missing.append(asset_id)
        if missing:
            raise AppError(
                ErrorCode.INVALID_PARAMS,
                "generation output asset not found in project",
                details={"asset_ids": missing},
            )
        for row in rows:
            metadata = dict(row.metadata or {})
            metadata.setdefault("project_id", scope.project_id)
            metadata.setdefault("episode_id", scope.episode_id)
            metadata.setdefault("node_id", node_id)
            if task_id is not None:
                metadata.setdefault("generation_task_id", task_id)
            updates: dict[str, object] = {"metadata": metadata}
            if row.project_id is None:
                updates["project_id"] = scope.project_id
            await Assets.filter(id=row.id).update(**updates)
        return ids

    async def apply_patch(
        self,
        scope: CanvasScope,
        ops: list[CanvasPatchOp],
        *,
        turn_id: str | None = None,
    ) -> CanvasPatchResponse:
        """按实体 revision CAS 应用画布补丁"""
        changed_nodes: list[CanvasNodeView] = []
        changed_edges: list[CanvasEdgeView] = []
        deleted_node_ids: list[UUID] = []
        deleted_edge_ids: list[UUID] = []

        async with in_transaction():
            episode = await ProjectEpisodes.select_for_update().filter(
                id=scope.episode_id,
                deleted_at__isnull=True,
            ).first()
            if episode is None:
                raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "episode not found")
            meta = await CanvasEpisodeMeta.select_for_update().filter(episode_id=scope.episode_id).first()
            if meta is None:
                raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "canvas episode meta not found")

            conflicts = await self._collect_patch_conflicts(scope.episode_id, ops)
            if conflicts:
                raise CanvasRevisionConflictError(conflicts)

            now = datetime.now(timezone.utc)

            for op in ops:
                if isinstance(op, CreateNodeOp):
                    payload = op.node
                    node_id = uuid4()
                    if payload.task_id is not None:
                        raise AppError(ErrorCode.INVALID_PARAMS, "task_id is managed by generation projection")
                    output_asset_ids = await self._require_project_output_asset_ids(scope, payload.output_asset_ids)
                    row = await CanvasNodes.create(
                        id=node_id,
                        episode_id=scope.episode_id,
                        kind=payload.kind,
                        revision=1,
                        position_x=payload.position.x,
                        position_y=payload.position.y,
                        title=payload.title,
                        input_prompt=payload.input_prompt,
                        output_text=payload.output_text,
                        status=payload.status,
                        model_id=payload.model_id,
                        voice_id=payload.voice_id,
                        ratio=payload.ratio,
                        duration_sec=payload.duration_sec,
                        resolution=payload.resolution,
                        task_id=None,
                        output_asset_ids=output_asset_ids,
                        error_message=payload.error_message,
                    )
                    meta.node_count += 1
                    changed_nodes.append(node_view_from_row(row))
                    if op.connect_anchor is not None:
                        anchor = op.connect_anchor
                        source_id = anchor.node_id if anchor.side == "right" else node_id
                        target_id = node_id if anchor.side == "right" else anchor.node_id
                        await self._require_nodes(scope.episode_id, [source_id, target_id])
                        edge_row = await CanvasEdges.create(
                            id=uuid4(),
                            episode_id=scope.episode_id,
                            revision=1,
                            source_node_id=source_id,
                            target_node_id=target_id,
                            source_port=anchor.source_port,
                            target_port=anchor.target_port,
                            edge_type=anchor.edge_type,
                            metadata=anchor.metadata,
                        )
                        meta.edge_count += 1
                        changed_edges.append(_edge_view(edge_row))
                elif isinstance(op, UpdateNodeOp):
                    row = await self._get_node_for_update(scope.episode_id, op.node_id)
                    self._require_entity_revision(row, op.expected_revision, kind="node", entity_id=op.node_id)
                    patch = op.patch
                    fields_set = patch.model_fields_set
                    if "position" in fields_set and patch.position is not None:
                        row.position_x = patch.position.x
                        row.position_y = patch.position.y
                    if "title" in fields_set:
                        row.title = patch.title
                    if "input_prompt" in fields_set:
                        row.input_prompt = patch.input_prompt
                    if "output_text" in fields_set:
                        row.output_text = patch.output_text
                    if "status" in fields_set:
                        row.status = patch.status
                    if "model_id" in fields_set:
                        row.model_id = patch.model_id
                    if "voice_id" in fields_set:
                        row.voice_id = patch.voice_id
                    if "ratio" in fields_set:
                        row.ratio = patch.ratio
                    if "duration_sec" in fields_set:
                        row.duration_sec = patch.duration_sec
                    if "resolution" in fields_set:
                        row.resolution = patch.resolution
                    if "task_id" in fields_set:
                        raise AppError(ErrorCode.INVALID_PARAMS, "task_id is managed by generation projection")
                    if "output_asset_ids" in fields_set:
                        row.output_asset_ids = await self._require_project_output_asset_ids(
                            scope,
                            patch.output_asset_ids,
                        )
                    if "error_message" in fields_set:
                        row.error_message = patch.error_message
                    if "kind" in fields_set:
                        row.kind = patch.kind
                    row.revision = row.revision + 1
                    await row.save()
                    changed_nodes.append(node_view_from_row(row))
                elif isinstance(op, DeleteNodeOp):
                    row = await self._get_node_for_update(scope.episode_id, op.node_id)
                    self._require_entity_revision(row, op.expected_revision, kind="node", entity_id=op.node_id)
                    row.deleted_at = now
                    row.revision = row.revision + 1
                    await row.save()
                    meta.node_count = max(0, meta.node_count - 1)
                    deleted_node_ids.append(op.node_id)
                    cascade_edges = await CanvasEdges.select_for_update().filter(
                        episode_id=scope.episode_id,
                        deleted_at__isnull=True,
                    ).filter(Q(source_node_id=row.id) | Q(target_node_id=row.id))
                    for e in cascade_edges:
                        e.deleted_at = now
                        e.revision = e.revision + 1
                        await e.save()
                        meta.edge_count = max(0, meta.edge_count - 1)
                        deleted_edge_ids.append(e.id)
                elif isinstance(op, ConnectNodesOp):
                    payload = op.edge
                    await self._require_nodes(scope.episode_id, [payload.source, payload.target])
                    row = await CanvasEdges.create(
                        id=uuid4(),
                        episode_id=scope.episode_id,
                        revision=1,
                        source_node_id=payload.source,
                        target_node_id=payload.target,
                        source_port=payload.source_port,
                        target_port=payload.target_port,
                        edge_type=payload.edge_type,
                        metadata=payload.metadata,
                    )
                    meta.edge_count += 1
                    changed_edges.append(_edge_view(row))
                elif isinstance(op, DisconnectNodesOp):
                    try:
                        row = await CanvasEdges.select_for_update().get(
                            id=op.edge_id,
                            episode_id=scope.episode_id,
                            deleted_at__isnull=True,
                        )
                    except DoesNotExist as exc:
                        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, f"edge {op.edge_id} not found") from exc
                    self._require_entity_revision(row, op.expected_revision, kind="edge", entity_id=op.edge_id)
                    row.deleted_at = now
                    row.revision = row.revision + 1
                    await row.save()
                    meta.edge_count = max(0, meta.edge_count - 1)
                    deleted_edge_ids.append(op.edge_id)
                else:
                    raise AssertionError(f"unsupported canvas patch operation: {type(op)!r}")

            await meta.save()
            await ProjectEpisodes.filter(id=scope.episode_id, deleted_at__isnull=True).update(
                updated_at=datetime.now(timezone.utc)
            )
            op_uuid = uuid4()
            op_id = op_uuid
            await CanvasOperations.create(
                op_id=op_uuid,
                episode_id=scope.episode_id,
                user_id=scope.user_id,
                turn_id=turn_id,
                op_type=CANVAS_OPERATION_TYPE_APPLY_PATCH,
                payload={"ops": [op.model_dump(mode="json") for op in ops]},
                status=CANVAS_OPERATION_STATUS_APPLIED,
            )

        await refresh_node_asset_urls(changed_nodes, scope=scope)
        result = CanvasPatchResponse(
            op_id=op_id,
            nodes=changed_nodes,
            edges=changed_edges,
            deleted_node_ids=deleted_node_ids,
            deleted_edge_ids=deleted_edge_ids,
        )
        await publish_canvas_patch(scope.episode_id, result)
        return result

    def _require_entity_revision(
        self,
        row: CanvasNodes | CanvasEdges,
        expected_revision: int,
        *,
        kind: Literal["node", "edge"],
        entity_id: UUID,
    ) -> None:
        """单条 op 应用时再次核对实体 revision"""
        if row.revision != expected_revision:
            raise CanvasRevisionConflictError(
                [
                    CanvasRevisionConflictItem(
                        kind=kind,
                        id=entity_id,
                        expected_revision=expected_revision,
                        actual_revision=row.revision,
                    )
                ]
            )

    async def _collect_patch_conflicts(
        self,
        episode_id: int,
        ops: list[CanvasPatchOp],
    ) -> list[CanvasRevisionConflictItem]:
        """按 ops 顺序模拟 working revision 与级联删边, 预检全部 CAS 冲突"""
        conflicts: list[CanvasRevisionConflictItem] = []
        node_working: dict[UUID, int] = {}
        edge_working: dict[UUID, int] = {}
        deleted_nodes: set[UUID] = set()
        deleted_edges: set[UUID] = set()
        for op in ops:
            if isinstance(op, UpdateNodeOp) or isinstance(op, DeleteNodeOp):
                if op.node_id in deleted_nodes:
                    raise AppError(ErrorCode.RESOURCE_NOT_FOUND, f"node {op.node_id} not found")
                if op.node_id not in node_working:
                    row = await self._get_node_for_update(episode_id, op.node_id)
                    node_working[op.node_id] = row.revision
                actual = node_working[op.node_id]
                if actual != op.expected_revision:
                    conflicts.append(
                        CanvasRevisionConflictItem(
                            kind="node",
                            id=op.node_id,
                            expected_revision=op.expected_revision,
                            actual_revision=actual,
                        )
                    )
                    continue
                node_working[op.node_id] = actual + 1
                if isinstance(op, DeleteNodeOp):
                    deleted_nodes.add(op.node_id)
                    cascade_edges = await CanvasEdges.select_for_update().filter(
                        episode_id=episode_id,
                        deleted_at__isnull=True,
                    ).filter(Q(source_node_id=op.node_id) | Q(target_node_id=op.node_id))
                    for edge in cascade_edges:
                        if edge.id in deleted_edges:
                            continue
                        deleted_edges.add(edge.id)
                        edge_working.pop(edge.id, None)
            elif isinstance(op, DisconnectNodesOp):
                if op.edge_id in deleted_edges:
                    raise AppError(ErrorCode.RESOURCE_NOT_FOUND, f"edge {op.edge_id} not found")
                if op.edge_id not in edge_working:
                    try:
                        row = await CanvasEdges.select_for_update().get(
                            id=op.edge_id,
                            episode_id=episode_id,
                            deleted_at__isnull=True,
                        )
                    except DoesNotExist as exc:
                        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, f"edge {op.edge_id} not found") from exc
                    edge_working[op.edge_id] = row.revision
                actual = edge_working[op.edge_id]
                if actual != op.expected_revision:
                    conflicts.append(
                        CanvasRevisionConflictItem(
                            kind="edge",
                            id=op.edge_id,
                            expected_revision=op.expected_revision,
                            actual_revision=actual,
                        )
                    )
                else:
                    edge_working[op.edge_id] = actual + 1
                    deleted_edges.add(op.edge_id)
        return conflicts

    async def update_node_generation(
        self,
        episode_id: int,
        node_id: str,
        *,
        task_id: int | None,
        status: CanvasNodeStatus,
        output_asset_ids: list[int] | None = None,
        error_message: str | None = None,
        model_id: str | None = None,
        voice_id: str | None = None,
        duration_sec: int | None = None,
        ratio: str | None = None,
        resolution: str | None = None,
        expected_revision: int | None = None,
        scope: CanvasScope | None = None,
    ) -> tuple[int, CanvasNodeView]:
        """写回节点生成状态并 bump 节点 revision"""
        async with in_transaction():
            # 锁顺序与 claim/apply_patch 一致: episode → meta → node
            episode = await ProjectEpisodes.select_for_update().filter(
                id=episode_id,
                deleted_at__isnull=True,
            ).first()
            if episode is None:
                raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "episode not found")
            # meta 无字段变更; 仍加锁以保持与 apply_patch 一致的锁顺序
            meta = await CanvasEpisodeMeta.select_for_update().filter(episode_id=episode_id).first()
            if meta is None:
                raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "canvas episode meta not found")
            row = await CanvasNodes.select_for_update().filter(
                id=UUID(node_id),
                episode_id=episode_id,
                deleted_at__isnull=True,
            ).first()
            if row is None:
                raise AppError(ErrorCode.RESOURCE_NOT_FOUND, f"node {node_id} not found")
            if expected_revision is not None and row.revision != expected_revision:
                raise CanvasRevisionConflictError(
                    [
                        CanvasRevisionConflictItem(
                            kind="node",
                            id=UUID(node_id),
                            expected_revision=expected_revision,
                            actual_revision=row.revision,
                        )
                    ]
                )
            row.task_id = task_id
            row.status = status.value
            if output_asset_ids is not None:
                if scope is None:
                    raise AppError(ErrorCode.INTERNAL_ERROR, "canvas output asset scope required")
                output_asset_ids = await self._attach_generation_output_assets(
                    scope,
                    output_asset_ids,
                    node_id=node_id,
                    task_id=task_id,
                )
                row.output_asset_ids = output_asset_ids
            if error_message is not None:
                row.error_message = error_message
            if model_id is not None:
                row.model_id = model_id
            if voice_id is not None:
                row.voice_id = voice_id
            if duration_sec is not None:
                row.duration_sec = duration_sec
            if ratio is not None:
                row.ratio = ratio
            if resolution is not None:
                row.resolution = resolution
            row.revision = row.revision + 1
            await row.save()
            episode.updated_at = datetime.now(timezone.utc)
            await episode.save(update_fields=["updated_at"])
            rev = row.revision
            node_view = node_view_from_row(row)
        if scope is not None:
            await refresh_node_asset_urls([node_view], scope=scope)
        return rev, node_view

    async def update_node_text_output(
        self,
        episode_id: int,
        node_id: str,
        *,
        status: CanvasNodeStatus,
        output_text: str | None = None,
        error_message: str | None = None,
        model_id: str | None = None,
        expected_revision: int | None = None,
    ) -> tuple[int, CanvasNodeView]:
        """写回文本节点输出并 bump 节点 revision"""
        async with in_transaction():
            # 锁顺序与 claim/apply_patch 一致: episode → meta → node
            episode = await ProjectEpisodes.select_for_update().filter(
                id=episode_id,
                deleted_at__isnull=True,
            ).first()
            if episode is None:
                raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "episode not found")
            meta = await CanvasEpisodeMeta.select_for_update().filter(episode_id=episode_id).first()
            if meta is None:
                raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "canvas episode meta not found")
            row = await CanvasNodes.select_for_update().filter(
                id=UUID(node_id),
                episode_id=episode_id,
                deleted_at__isnull=True,
            ).first()
            if row is None:
                raise AppError(ErrorCode.RESOURCE_NOT_FOUND, f"node {node_id} not found")
            if expected_revision is not None and row.revision != expected_revision:
                raise CanvasRevisionConflictError(
                    [
                        CanvasRevisionConflictItem(
                            kind="node",
                            id=UUID(node_id),
                            expected_revision=expected_revision,
                            actual_revision=row.revision,
                        )
                    ]
                )
            row.status = status.value
            row.task_id = None
            if output_text is not None:
                row.output_text = output_text
            if error_message is not None:
                row.error_message = error_message
            if model_id is not None:
                row.model_id = model_id
            row.revision = row.revision + 1
            await row.save()
            episode.updated_at = datetime.now(timezone.utc)
            await episode.save(update_fields=["updated_at"])
            rev = row.revision
            node_view = node_view_from_row(row)
        return rev, node_view

    async def _require_nodes(self, episode_id: int, node_ids: list[UUID]) -> None:
        """确认节点均存在且未删除"""
        unique_ids = list(dict.fromkeys(node_ids))
        count = await CanvasNodes.filter(
            id__in=unique_ids,
            episode_id=episode_id,
            deleted_at__isnull=True,
        ).count()
        if count != len(unique_ids):
            raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "node not found")

    async def _get_node_for_update(self, episode_id: int, node_id: UUID) -> CanvasNodes:
        """行锁读取待更新节点"""
        try:
            return await CanvasNodes.select_for_update().get(
                id=node_id,
                episode_id=episode_id,
                deleted_at__isnull=True,
            )
        except DoesNotExist as exc:
            raise AppError(ErrorCode.RESOURCE_NOT_FOUND, f"node {node_id} not found") from exc


canvas_service = CanvasService()
