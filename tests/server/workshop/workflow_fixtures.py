"""工坊 workflow DAG 测试样例。"""

from __future__ import annotations

from app.server.workshop.domain.enums import (
    WorkshopArtifactStorageType,
    WorkshopToolCapability,
)
from app.server.workshop.domain.workflow_definition import (
    NodeAssignee,
    NodeInput,
    NodeOutput,
    WorkflowEdge,
    WorkflowNode,
)


def linear_research_copy_graph() -> tuple[
    tuple[WorkflowNode, ...], tuple[WorkflowEdge, ...]
]:
    """两步线性：竞品简报 → 文案"""
    nodes = (
        WorkflowNode(
            id="n_research",
            title="竞品调研",
            instruction="输出竞品简报",
            assignee=NodeAssignee(preset_key="ecom_market_competitor_advisor"),
            outputs=(
                NodeOutput(
                    name="brief",
                    storage_type=WorkshopArtifactStorageType.DB,
                ),
            ),
            external_capabilities=(WorkshopToolCapability.WEB_SEARCH,),
        ),
        WorkflowNode(
            id="n_copy",
            title="主图文案",
            instruction="根据简报写文案",
            assignee=NodeAssignee(preset_key="ecom_listing_planner_executor"),
            inputs=(
                NodeInput(kind="artifact", name="brief", from_node_id="n_research"),
            ),
            outputs=(
                NodeOutput(
                    name="copy",
                    storage_type=WorkshopArtifactStorageType.DB,
                ),
            ),
        ),
    )
    edges = (WorkflowEdge(from_id="n_research", to_id="n_copy"),)
    return nodes, edges


def single_step_graph(
    *,
    preset: str = "ecom_market_competitor_advisor",
    title: str = "一步",
    caps: tuple[WorkshopToolCapability, ...] = (),
) -> tuple[tuple[WorkflowNode, ...], tuple[WorkflowEdge, ...]]:
    """单节点图"""
    nodes = (
        WorkflowNode(
            id="n1",
            title=title,
            instruction=title,
            assignee=NodeAssignee(preset_key=preset),
            outputs=(
                NodeOutput(
                    name=f"{title}_out",
                    storage_type=WorkshopArtifactStorageType.DB,
                ),
            ),
            external_capabilities=caps,
        ),
    )
    return nodes, ()
