from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from tortoise.exceptions import DoesNotExist
from tortoise.transactions import in_transaction

from app.server.assets.services.service import asset_service
from app.server.canvas.schemas.api import (
    CanvasEdgeView,
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
from app.server.canvas.domain.constants import (
    CANVAS_OPERATION_STATUS_APPLIED,
    CANVAS_OPERATION_TYPE_APPLY_PATCH,
)
from app.server.canvas.domain.enums import CanvasNodeStatus
from app.server.exceptions.base import AppError
from app.server.exceptions.codes import ErrorCode
from app.server.assets.persistence.assets import Assets
from app.server.canvas.persistence.edges import CanvasEdges
from app.server.canvas.persistence.nodes import CanvasNodes
from app.server.canvas.persistence.operations import CanvasOperations
from app.server.canvas.persistence.project_meta import CanvasProjectMeta


class CanvasRevisionConflictError(AppError):
    """旧 revision 写画布时抛出, 触发前端或 Agent 重新查询"""

    def __init__(self, expected: int, actual: int) -> None:
        """记录期望 revision 与实际 revision"""
        super().__init__(
            ErrorCode.CANVAS_REVISION_CONFLICT,
            "canvas revision conflict",
            details={
                "expected_revision": expected,
                "actual_revision": actual,
                "error_type": "revision_conflict",
            },
        )


def node_view_from_row(row: CanvasNodes) -> CanvasNodeView:
    """数据库节点行转前端与工具共用的节点视图"""
    output_asset_ids = row.output_asset_ids
    output_asset_list = [int(item) for item in output_asset_ids] if isinstance(output_asset_ids, list) else None
    return CanvasNodeView(
        id=row.id,
        kind=row.kind,
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
    """数据库连线行转带端口语义的依赖边视图"""
    return CanvasEdgeView(
        id=row.id,
        source=row.source_node_id,
        target=row.target_node_id,
        source_port=row.source_port,
        target_port=row.target_port,
        edge_type=row.edge_type,
        metadata=row.metadata,
    )


async def refresh_node_asset_urls(nodes: list[CanvasNodeView]) -> None:
    """把节点 output_asset_ids 解析为最新预览 URL"""
    asset_ids = {
        int(asset_id)
        for node in nodes
        for asset_id in (node.output_asset_ids or [])
    }
    assets = await Assets.filter(id__in=list(asset_ids), deleted_at__isnull=True).all() if asset_ids else []
    assets_by_id = {int(row.id): row for row in assets}
    for node in nodes:
        urls = [
            asset_service.preview_url(assets_by_id[asset_id].storage_key)
            for asset_id in (node.output_asset_ids or [])
            if asset_id in assets_by_id
        ]
        node.output_asset_urls = urls or None


class CanvasService:
    """画布节点与连线的原子读写服务"""

    async def ensure_meta(self, project_id: int) -> CanvasProjectMeta:
        """确保项目存在 revision 元信息行"""
        meta = await CanvasProjectMeta.filter(project_id=project_id).first()
        if meta is not None:
            return meta
        return await CanvasProjectMeta.create(project_id=project_id, revision=0)

    async def get_snapshot(self, project_id: int) -> CanvasSnapshot:
        """读取 revision, 未删除节点, 未删除连线"""
        await self.ensure_meta(project_id)
        meta = await CanvasProjectMeta.get(project_id=project_id)
        nodes = await CanvasNodes.filter(project_id=project_id, deleted_at__isnull=True).all()
        edges = await CanvasEdges.filter(project_id=project_id, deleted_at__isnull=True).all()
        node_views = [node_view_from_row(n) for n in nodes]
        await refresh_node_asset_urls(node_views)
        return CanvasSnapshot(
            revision=int(meta.revision),
            nodes=node_views,
            edges=[_edge_view(e) for e in edges],
        )

    async def refresh_node_asset_urls(self, nodes: list[CanvasNodeView]) -> None:
        """实例方法入口, 委托模块级 refresh_node_asset_urls"""
        await refresh_node_asset_urls(nodes)

    async def apply_patch(
        self,
        project_id: int,
        ops: list[CanvasPatchOp],
        expected_revision: int,
        *,
        user_id: int | None = None,
        turn_id: str | None = None,
    ) -> CanvasPatchResponse:
        """单事务应用补丁, revision CAS 防并发覆盖"""
        changed_nodes: list[CanvasNodeView] = []
        changed_edges: list[CanvasEdgeView] = []
        deleted_node_ids: list[UUID] = []
        deleted_edge_ids: list[UUID] = []

        async with in_transaction():
            # 锁 meta 行后校验 revision, 避免并发补丁交叉写入
            meta = await CanvasProjectMeta.select_for_update().get_or_none(project_id=project_id)
            if meta is None:
                meta = await CanvasProjectMeta.create(project_id=project_id, revision=0)
                meta = await CanvasProjectMeta.select_for_update().get(project_id=project_id)

            if int(meta.revision) != int(expected_revision):
                raise CanvasRevisionConflictError(expected_revision, int(meta.revision))

            revision_before = int(meta.revision)
            now = datetime.now(timezone.utc)

            for op in ops:
                if isinstance(op, CreateNodeOp):
                    payload = op.node
                    node_id = uuid4()
                    row = await CanvasNodes.create(
                        id=node_id,
                        project_id=project_id,
                        kind=payload.kind,
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
                        task_id=payload.task_id,
                        output_asset_ids=payload.output_asset_ids,
                        error_message=payload.error_message,
                    )
                    meta.node_count += 1
                    changed_nodes.append(node_view_from_row(row))
                    if op.connect_anchor is not None:
                        anchor = op.connect_anchor
                        if anchor.side == "right":
                            source_id = anchor.node_id
                            target_id = node_id
                        else:
                            source_id = node_id
                            target_id = anchor.node_id
                        edge_id = uuid4()
                        edge_row = await CanvasEdges.create(
                            id=edge_id,
                            project_id=project_id,
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
                    try:
                        row = await CanvasNodes.select_for_update().get(
                            id=op.node_id,
                            project_id=project_id,
                            deleted_at__isnull=True,
                        )
                    except DoesNotExist as exc:
                        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, f"node {op.node_id} not found") from exc
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
                        row.task_id = patch.task_id
                    if "output_asset_ids" in fields_set:
                        row.output_asset_ids = patch.output_asset_ids
                    if "error_message" in fields_set:
                        row.error_message = patch.error_message
                    if "kind" in fields_set:
                        row.kind = patch.kind
                    await row.save()
                    changed_nodes.append(node_view_from_row(row))
                elif isinstance(op, DeleteNodeOp):
                    try:
                        row = await CanvasNodes.select_for_update().get(
                            id=op.node_id,
                            project_id=project_id,
                            deleted_at__isnull=True,
                        )
                    except DoesNotExist as exc:
                        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, f"node {op.node_id} not found") from exc
                    row.deleted_at = now
                    await row.save()
                    meta.node_count = max(0, meta.node_count - 1)
                    deleted_node_ids.append(op.node_id)
                    # 删节点时软删除相关连线, 保持图可解析
                    for e in await CanvasEdges.filter(
                        project_id=project_id,
                        deleted_at__isnull=True,
                        source_node_id=row.id,
                    ):
                        e.deleted_at = now
                        await e.save()
                        meta.edge_count = max(0, meta.edge_count - 1)
                        deleted_edge_ids.append(e.id)
                    for e in await CanvasEdges.filter(
                        project_id=project_id,
                        deleted_at__isnull=True,
                        target_node_id=row.id,
                    ):
                        if e.deleted_at is None:
                            e.deleted_at = now
                            await e.save()
                            meta.edge_count = max(0, meta.edge_count - 1)
                            deleted_edge_ids.append(e.id)
                elif isinstance(op, ConnectNodesOp):
                    payload = op.edge
                    edge_id = uuid4()
                    row = await CanvasEdges.create(
                        id=edge_id,
                        project_id=project_id,
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
                            project_id=project_id,
                            deleted_at__isnull=True,
                        )
                    except DoesNotExist as exc:
                        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, f"edge {op.edge_id} not found") from exc
                    row.deleted_at = now
                    await row.save()
                    meta.edge_count = max(0, meta.edge_count - 1)
                    deleted_edge_ids.append(op.edge_id)
                else:
                    raise AssertionError(f"unsupported canvas patch operation: {type(op)!r}")

            meta.revision = int(meta.revision) + 1
            await meta.save()
            op_id: str | None = None
            if user_id is not None:
                # 写审计日志, 记录本轮补丁内容
                op_uuid = uuid4()
                op_id = op_uuid
                await CanvasOperations.create(
                    op_id=op_uuid,
                    project_id=project_id,
                    user_id=user_id,
                    turn_id=turn_id,
                    op_type=CANVAS_OPERATION_TYPE_APPLY_PATCH,
                    payload={"ops": [op.model_dump(mode="json") for op in ops]},
                    status=CANVAS_OPERATION_STATUS_APPLIED,
                    revision_before=revision_before,
                    revision_after=int(meta.revision),
                )

        meta = await CanvasProjectMeta.get(project_id=project_id)
        await refresh_node_asset_urls(changed_nodes)
        return CanvasPatchResponse(
            revision=int(meta.revision),
            op_id=op_id,
            nodes=changed_nodes,
            edges=changed_edges,
            deleted_node_ids=deleted_node_ids,
            deleted_edge_ids=deleted_edge_ids,
        )

    async def update_node_generation(
        self,
        project_id: int,
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
    ) -> tuple[int, CanvasNodeView]:
        """回写节点生成状态与产物, 推进 revision, 不提交网关任务"""
        async with in_transaction():
            meta = await CanvasProjectMeta.select_for_update().get(project_id=project_id)
            if expected_revision is not None and int(meta.revision) != int(expected_revision):
                raise CanvasRevisionConflictError(expected_revision, int(meta.revision))
            row = await CanvasNodes.select_for_update().get(
                id=UUID(node_id),
                project_id=project_id,
                deleted_at__isnull=True,
            )
            row.task_id = task_id
            row.status = status.value
            if output_asset_ids is not None:
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
            await row.save()
            meta.revision = int(meta.revision) + 1
            await meta.save()
            rev = int(meta.revision)
        row = await CanvasNodes.get(id=UUID(node_id))
        node_view = node_view_from_row(row)
        await refresh_node_asset_urls([node_view])
        return rev, node_view

    async def update_node_text_output(
        self,
        project_id: int,
        node_id: str,
        *,
        status: CanvasNodeStatus,
        output_text: str | None = None,
        error_message: str | None = None,
        model_id: str | None = None,
        expected_revision: int | None = None,
    ) -> tuple[int, CanvasNodeView]:
        """写回文本节点生成结果，推进 revision。"""
        async with in_transaction():
            meta = await CanvasProjectMeta.select_for_update().get(project_id=project_id)
            if expected_revision is not None and int(meta.revision) != int(expected_revision):
                raise CanvasRevisionConflictError(expected_revision, int(meta.revision))
            row = await CanvasNodes.select_for_update().get(
                id=UUID(node_id),
                project_id=project_id,
                deleted_at__isnull=True,
            )
            row.status = status.value
            row.task_id = None
            if output_text is not None:
                row.output_text = output_text
            if error_message is not None:
                row.error_message = error_message
            if model_id is not None:
                row.model_id = model_id
            await row.save()
            meta.revision = int(meta.revision) + 1
            await meta.save()
            rev = int(meta.revision)
        row = await CanvasNodes.get(id=UUID(node_id))
        node_view = node_view_from_row(row)
        await refresh_node_asset_urls([node_view])
        return rev, node_view


canvas_service = CanvasService()
