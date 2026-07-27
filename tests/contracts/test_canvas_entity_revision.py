from __future__ import annotations

from uuid import uuid4

import pytest
from tortoise import Tortoise

from app.contracts.canvas import DeleteNodeOp, DisconnectNodesOp, UpdateNodeOp, UpdateNodePatch
from app.server.canvas.persistence.edges import CanvasEdges
from app.server.canvas.persistence.episode_meta import CanvasEpisodeMeta
from app.server.canvas.persistence.nodes import CanvasNodes
from app.server.canvas.services.canvas_service import CanvasRevisionConflictError, canvas_service
from app.server.exceptions.base import AppError
from app.server.exceptions.codes import ErrorCode
from app.server.projects.domain.models import CanvasScope
from app.server.projects.persistence.episodes import ProjectEpisodes
from app.server.projects.persistence.projects import Projects

MODELS = [
    "app.server.canvas.persistence.edges",
    "app.server.canvas.persistence.episode_meta",
    "app.server.canvas.persistence.nodes",
    "app.server.canvas.persistence.operations",
    "app.server.projects.persistence.episodes",
    "app.server.projects.persistence.projects",
]


async def _init_db() -> None:
    """初始化内存 SQLite 与 schema"""
    await Tortoise.init(db_url="sqlite://:memory:", modules={"models": MODELS})
    await Tortoise.generate_schemas()


async def _close_db() -> None:
    """关闭 Tortoise 连接"""
    await Tortoise.close_connections()


async def _scope() -> tuple[CanvasScope, CanvasNodes]:
    """创建最小项目/集/节点并返回 scope 与节点行"""
    project = await Projects.create(
        owner_user_id="1",
        name="项目",
        status=1,
        cover_asset_id=None,
        tone_constraint={},
        style_constraint={},
        config={},
    )
    episode = await ProjectEpisodes.create(
        project_id=int(project.id),
        creator_id=1,
        episode_no=1,
        name="第 1 集",
        cover_asset_id=None,
    )
    await CanvasEpisodeMeta.create(episode_id=int(episode.id))
    node = await CanvasNodes.create(
        id=uuid4(),
        episode_id=int(episode.id),
        kind="text",
        revision=1,
        position_x=0,
        position_y=0,
        title="",
        input_prompt="",
        output_text="",
        status="idle",
    )
    return CanvasScope(project_id=int(project.id), episode_id=int(episode.id), user_id=1), node


@pytest.mark.asyncio
async def test_apply_patch_bumps_node_revision_on_success() -> None:
    """正确 expected_revision 时更新成功且节点 revision +1"""
    await _init_db()
    try:
        scope, node = await _scope()
        result = await canvas_service.apply_patch(
            scope,
            [
                UpdateNodeOp(
                    op="update_node",
                    node_id=node.id,
                    expected_revision=1,
                    patch=UpdateNodePatch(title="新标题"),
                )
            ],
        )
        assert len(result.nodes) == 1
        assert result.nodes[0].revision == 2
        assert result.nodes[0].title == "新标题"
        refreshed = await CanvasNodes.get(id=node.id)
        assert refreshed.revision == 2
        assert refreshed.title == "新标题"
    finally:
        await _close_db()


@pytest.mark.asyncio
async def test_apply_patch_revision_conflict_does_not_write() -> None:
    """错误 expected_revision 时 409 conflicts 且库不改"""
    await _init_db()
    try:
        scope, node = await _scope()
        with pytest.raises(CanvasRevisionConflictError) as exc_info:
            await canvas_service.apply_patch(
                scope,
                [
                    UpdateNodeOp(
                        op="update_node",
                        node_id=node.id,
                        expected_revision=99,
                        patch=UpdateNodePatch(title="不应写入"),
                    )
                ],
            )
        details = exc_info.value.details
        assert details["error_type"] == "revision_conflict"
        assert details["conflicts"] == [
            {
                "kind": "node",
                "id": str(node.id),
                "expected_revision": 99,
                "actual_revision": 1,
            }
        ]
        refreshed = await CanvasNodes.get(id=node.id)
        assert refreshed.revision == 1
        assert refreshed.title == ""
    finally:
        await _close_db()


@pytest.mark.asyncio
async def test_apply_patch_same_node_chained_ops_use_working_revision() -> None:
    """同 patch 内同一节点多条 op 按 working revision 串联 CAS"""
    await _init_db()
    try:
        scope, node = await _scope()
        result = await canvas_service.apply_patch(
            scope,
            [
                UpdateNodeOp(
                    op="update_node",
                    node_id=node.id,
                    expected_revision=1,
                    patch=UpdateNodePatch(title="一步"),
                ),
                UpdateNodeOp(
                    op="update_node",
                    node_id=node.id,
                    expected_revision=2,
                    patch=UpdateNodePatch(title="二步"),
                ),
            ],
        )
        assert result.nodes[-1].revision == 3
        assert result.nodes[-1].title == "二步"
        refreshed = await CanvasNodes.get(id=node.id)
        assert refreshed.revision == 3
        assert refreshed.title == "二步"
    finally:
        await _close_db()


@pytest.mark.asyncio
async def test_delete_then_disconnect_same_edge_fails_in_precheck() -> None:
    """同 patch 内 delete 级联边后再 disconnect, 预检即 404 与 apply 对齐"""
    await _init_db()
    try:
        scope, node = await _scope()
        other = await CanvasNodes.create(
            id=uuid4(),
            episode_id=scope.episode_id,
            kind="text",
            revision=1,
            position_x=10,
            position_y=0,
            title="",
            input_prompt="",
            output_text="",
            status="idle",
        )
        edge = await CanvasEdges.create(
            id=uuid4(),
            episode_id=scope.episode_id,
            revision=1,
            source_node_id=node.id,
            target_node_id=other.id,
            source_port="output_text",
            target_port="prompt_input",
            edge_type="dependency",
            metadata={},
        )
        meta = await CanvasEpisodeMeta.get(episode_id=scope.episode_id)
        meta.node_count = 2
        meta.edge_count = 1
        await meta.save()

        with pytest.raises(AppError) as exc_info:
            await canvas_service.apply_patch(
                scope,
                [
                    DeleteNodeOp(op="delete_node", node_id=node.id, expected_revision=1),
                    DisconnectNodesOp(op="disconnect", edge_id=edge.id, expected_revision=1),
                ],
            )
        assert exc_info.value.code == ErrorCode.RESOURCE_NOT_FOUND
        refreshed_node = await CanvasNodes.get(id=node.id)
        assert refreshed_node.deleted_at is None
        assert refreshed_node.revision == 1
        refreshed_edge = await CanvasEdges.get(id=edge.id)
        assert refreshed_edge.deleted_at is None
        assert refreshed_edge.revision == 1
    finally:
        await _close_db()
