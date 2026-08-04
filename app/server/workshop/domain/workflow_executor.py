"""工作流消费执行：按 DAG 调度节点并由注入的 NodeRunner 产出产物。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Optional, Protocol, Sequence

from app.server.workshop.domain.enums import (
    WorkshopArtifactStorageType,
    WorkshopWorkflowRunStatus,
)
from app.server.workshop.domain.workflow_definition import (
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowNode,
    validate_workflow_definition,
)


class WorkflowExecuteError(Exception):
    """工作流执行失败"""


@dataclass(frozen=True, slots=True)
class ProducedArtifact:
    """节点执行产出的产物"""

    name: str
    storage_type: WorkshopArtifactStorageType
    content: str = ""
    storage_key: str = ""
    size_bytes: Optional[int] = None

    def is_non_empty(self) -> bool:
        """判断产物是否非空"""
        if self.storage_type is WorkshopArtifactStorageType.DB:
            return bool(self.content.strip())
        return bool(self.storage_key.strip()) and (
            self.size_bytes is None or self.size_bytes > 0
        )


@dataclass(frozen=True, slots=True)
class NodeRunContext:
    """单节点执行上下文"""

    run_id: str
    project_id: str
    node: WorkflowNode
    input_artifacts: Mapping[str, ProducedArtifact]
    model_key: str


class NodeRunner(Protocol):
    """节点执行端口：由专家 runtime 或测试替身实现"""

    async def run_node(self, ctx: NodeRunContext) -> Sequence[ProducedArtifact]:
        """执行一个节点并返回产物"""


@dataclass(slots=True)
class WorkflowRunState:
    """内存中的一次运行状态（权威落库由调用方写入）"""

    run_id: str
    status: WorkshopWorkflowRunStatus
    current_node_id: Optional[str] = None
    error_message: Optional[str] = None
    artifacts: dict[str, ProducedArtifact] = field(default_factory=dict)
    completed_node_ids: list[str] = field(default_factory=list)


def topological_order(
    nodes: Sequence[WorkflowNode], edges: Sequence[WorkflowEdge]
) -> tuple[WorkflowNode, ...]:
    """按依赖排序节点；有环则抛错"""
    by_id = {node.id: node for node in nodes}
    indegree = {node.id: 0 for node in nodes}
    outgoing: dict[str, list[str]] = {node.id: [] for node in nodes}
    for edge in edges:
        if edge.from_id not in by_id or edge.to_id not in by_id:
            raise WorkflowExecuteError("edge endpoint unknown")
        outgoing[edge.from_id].append(edge.to_id)
        indegree[edge.to_id] += 1
    queue = [node_id for node_id, degree in indegree.items() if degree == 0]
    ordered: list[WorkflowNode] = []
    while queue:
        # 稳定：同层按 id 排序
        queue.sort()
        current = queue.pop(0)
        ordered.append(by_id[current])
        for nxt in outgoing[current]:
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                queue.append(nxt)
    if len(ordered) != len(nodes):
        raise WorkflowExecuteError("cycle in workflow graph")
    return tuple(ordered)


async def execute_workflow_graph(
    *,
    run_id: str,
    project_id: str,
    nodes: Sequence[WorkflowNode],
    edges: Sequence[WorkflowEdge],
    model_key: str,
    known_preset_keys: frozenset[str],
    preset_allowlists: Mapping[str, frozenset],
    runner: NodeRunner,
) -> WorkflowRunState:
    """校验定义后按拓扑顺序执行节点并验收产物"""
    resolved_model = model_key.strip()
    if not resolved_model:
        state = WorkflowRunState(
            run_id=run_id,
            status=WorkshopWorkflowRunStatus.FAILED,
            error_message="workflow model_key required",
        )
        return state
    validate_workflow_definition(
        WorkflowDefinition(
            nodes=tuple(nodes),
            edges=tuple(edges),
            known_preset_keys=known_preset_keys,
            preset_allowlists=preset_allowlists,
        )
    )
    state = WorkflowRunState(
        run_id=run_id,
        status=WorkshopWorkflowRunStatus.RUNNING,
    )
    try:
        order = topological_order(nodes, edges)
    except WorkflowExecuteError as exc:
        state.status = WorkshopWorkflowRunStatus.FAILED
        state.error_message = str(exc)
        return state

    for node in order:
        state.current_node_id = node.id
        input_artifacts: dict[str, ProducedArtifact] = {}
        for inp in node.inputs:
            if inp.kind != "artifact":
                continue
            artifact = state.artifacts.get(inp.name)
            if artifact is None:
                state.status = WorkshopWorkflowRunStatus.FAILED
                state.error_message = f"missing input artifact: {inp.name}"
                return state
            input_artifacts[inp.name] = artifact
        try:
            produced = await runner.run_node(
                NodeRunContext(
                    run_id=run_id,
                    project_id=project_id,
                    node=node,
                    input_artifacts=input_artifacts,
                    model_key=resolved_model,
                )
            )
        except Exception as exc:  # noqa: BLE001 — 节点失败收束为运行失败
            if node.on_failure == "block":
                state.status = WorkshopWorkflowRunStatus.BLOCKED
            else:
                state.status = WorkshopWorkflowRunStatus.FAILED
            state.error_message = str(exc)
            return state

        # 中间产物由执行自然产生；节点声明端口不作硬验收
        for item in produced:
            if not item.is_non_empty():
                continue
            if item.name in state.artifacts:
                state.status = WorkshopWorkflowRunStatus.FAILED
                state.error_message = f"duplicate artifact name: {item.name}"
                return state
            state.artifacts[item.name] = item
        state.completed_node_ids.append(node.id)

    if not _has_final_file_deliverable(state.artifacts.values()):
        state.status = WorkshopWorkflowRunStatus.FAILED
        state.error_message = "missing final file deliverable"
        state.current_node_id = None
        return state

    state.status = WorkshopWorkflowRunStatus.SUCCEEDED
    state.current_node_id = None
    return state


def _has_final_file_deliverable(artifacts: Sequence[ProducedArtifact]) -> bool:
    """整次任务成功条件：至少一个非空 oss 文件产物"""
    for item in artifacts:
        if item.storage_type is not WorkshopArtifactStorageType.OSS:
            continue
        if item.is_non_empty():
            return True
    return False
