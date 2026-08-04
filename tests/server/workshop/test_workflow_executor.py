"""工作流消费执行 seam。"""

from __future__ import annotations

from typing import Mapping, Sequence

import pytest

from app.server.workshop.domain.enums import (
    WorkshopArtifactStorageType,
    WorkshopToolCapability,
    WorkshopWorkflowRunStatus,
)
from app.server.workshop.domain.workflow_definition import (
    NodeAssignee,
    NodeInput,
    NodeOutput,
    WorkflowEdge,
    WorkflowNode,
)
from app.server.workshop.domain.workflow_executor import (
    NodeRunContext,
    ProducedArtifact,
    execute_workflow_graph,
)


class _ScriptedRunner:
    """按节点 id 返回预定产物"""

    def __init__(self, outputs: Mapping[str, Sequence[ProducedArtifact]]) -> None:
        self._outputs = outputs

    async def run_node(self, ctx: NodeRunContext) -> Sequence[ProducedArtifact]:
        """返回脚本化产物"""
        return self._outputs[ctx.node.id]


def _presets() -> tuple[frozenset[str], dict[str, frozenset[WorkshopToolCapability]]]:
    keys = frozenset(
        {"ecom_market_competitor_advisor", "ecom_listing_planner_executor"}
    )
    allow = {
        "ecom_market_competitor_advisor": frozenset(
            {WorkshopToolCapability.WEB_SEARCH}
        ),
        "ecom_listing_planner_executor": frozenset(),
    }
    return keys, allow


@pytest.mark.asyncio
async def test_execute_succeeds_with_final_file_deliverable() -> None:
    """整次 run 有 oss/文件类产物则成功；不强制节点 DB 端口"""
    nodes = (
        WorkflowNode(
            id="n1",
            title="research",
            instruction="research",
            assignee=NodeAssignee(preset_key="ecom_market_competitor_advisor"),
            external_capabilities=(WorkshopToolCapability.WEB_SEARCH,),
        ),
        WorkflowNode(
            id="n2",
            title="copy",
            instruction="write copy",
            assignee=NodeAssignee(preset_key="ecom_listing_planner_executor"),
            inputs=(NodeInput(kind="artifact", name="brief", from_node_id="n1"),),
        ),
    )
    edges = (WorkflowEdge(from_id="n1", to_id="n2"),)
    keys, allow = _presets()
    runner = _ScriptedRunner(
        {
            "n1": (
                ProducedArtifact(
                    name="brief",
                    storage_type=WorkshopArtifactStorageType.DB,
                    content="竞品简报",
                ),
            ),
            "n2": (
                ProducedArtifact(
                    name="script.txt",
                    storage_type=WorkshopArtifactStorageType.OSS,
                    storage_key="workshop/1/wp/run_1/x/script.txt",
                    size_bytes=12,
                ),
            ),
        }
    )

    state = await execute_workflow_graph(
        run_id="run_1",
        project_id="proj_1",
        nodes=nodes,
        edges=edges,
        model_key="test-model",
        known_preset_keys=keys,
        preset_allowlists=allow,
        runner=runner,
    )

    assert state.status is WorkshopWorkflowRunStatus.SUCCEEDED
    assert state.completed_node_ids == ["n1", "n2"]
    assert state.artifacts["script.txt"].storage_key.endswith("script.txt")


@pytest.mark.asyncio
async def test_execute_fails_when_only_db_artifacts() -> None:
    """仅有 DB 字符串产物、没有文件类交付 → fail"""
    nodes = (
        WorkflowNode(
            id="n1",
            title="research",
            instruction="research",
            assignee=NodeAssignee(preset_key="ecom_market_competitor_advisor"),
            outputs=(
                NodeOutput(
                    name="brief",
                    storage_type=WorkshopArtifactStorageType.DB,
                ),
            ),
        ),
    )
    keys, allow = _presets()
    runner = _ScriptedRunner(
        {
            "n1": (
                ProducedArtifact(
                    name="brief",
                    storage_type=WorkshopArtifactStorageType.DB,
                    content="only text",
                ),
            )
        }
    )

    state = await execute_workflow_graph(
        run_id="run_2",
        project_id="proj_1",
        nodes=nodes,
        edges=(),
        model_key="test-model",
        known_preset_keys=keys,
        preset_allowlists=allow,
        runner=runner,
    )

    assert state.status is WorkshopWorkflowRunStatus.FAILED
    assert state.error_message == "missing final file deliverable"


@pytest.mark.asyncio
async def test_execute_fails_when_no_artifacts() -> None:
    """无产物 → fail"""
    nodes = (
        WorkflowNode(
            id="n1",
            title="research",
            instruction="research",
            assignee=NodeAssignee(preset_key="ecom_market_competitor_advisor"),
        ),
    )
    keys, allow = _presets()
    runner = _ScriptedRunner({"n1": ()})

    state = await execute_workflow_graph(
        run_id="run_3",
        project_id="proj_1",
        nodes=nodes,
        edges=(),
        model_key="test-model",
        known_preset_keys=keys,
        preset_allowlists=allow,
        runner=runner,
    )

    assert state.status is WorkshopWorkflowRunStatus.FAILED
    assert state.error_message == "missing final file deliverable"


@pytest.mark.asyncio
async def test_execute_fails_when_only_filesystem_artifacts() -> None:
    """仅有本地 filesystem 指针不算成功；必须 oss"""
    nodes = (
        WorkflowNode(
            id="n1",
            title="write",
            instruction="write",
            assignee=NodeAssignee(preset_key="ecom_listing_planner_executor"),
        ),
    )
    keys, allow = _presets()
    runner = _ScriptedRunner(
        {
            "n1": (
                ProducedArtifact(
                    name="local.txt",
                    storage_type=WorkshopArtifactStorageType.FILESYSTEM,
                    storage_key="workflow_outputs/run_x/local.txt",
                    size_bytes=4,
                ),
            )
        }
    )

    state = await execute_workflow_graph(
        run_id="run_fs",
        project_id="proj_1",
        nodes=nodes,
        edges=(),
        model_key="test-model",
        known_preset_keys=keys,
        preset_allowlists=allow,
        runner=runner,
    )

    assert state.status is WorkshopWorkflowRunStatus.FAILED
    assert state.error_message == "missing final file deliverable"
