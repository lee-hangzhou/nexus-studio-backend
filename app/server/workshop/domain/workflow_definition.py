"""可执行工作流定义（JSON DAG）与保存前校验。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import FrozenSet, Mapping, Optional, Sequence, Tuple

from app.server.workshop.domain.enums import (
    WorkshopArtifactStorageType,
    WorkshopToolCapability,
)


class WorkflowDefinitionError(ValueError):
    """工作流定义非法（fail closed）"""


@dataclass(frozen=True, slots=True)
class NodeAssignee:
    """节点执行人：以 preset_key 绑定，跑时再落到名册"""

    preset_key: str

    def __post_init__(self) -> None:
        key = self.preset_key.strip()
        if not key:
            raise WorkflowDefinitionError("assignee.preset_key required")
        object.__setattr__(self, "preset_key", key)


@dataclass(frozen=True, slots=True)
class NodeInput:
    """节点输入契约"""

    kind: str
    name: str = ""
    from_node_id: Optional[str] = None

    def __post_init__(self) -> None:
        kind = self.kind.strip()
        if kind not in {"artifact", "project_brief"}:
            raise WorkflowDefinitionError(f"unsupported input kind: {kind}")
        object.__setattr__(self, "kind", kind)
        if kind == "artifact":
            name = self.name.strip()
            if not name:
                raise WorkflowDefinitionError("artifact input name required")
            object.__setattr__(self, "name", name)
            if self.from_node_id is not None:
                from_id = self.from_node_id.strip()
                if not from_id:
                    raise WorkflowDefinitionError("from_node_id required when set")
                object.__setattr__(self, "from_node_id", from_id)


@dataclass(frozen=True, slots=True)
class NodeOutput:
    """节点产物契约"""

    name: str
    storage_type: WorkshopArtifactStorageType
    required: bool = True

    def __post_init__(self) -> None:
        name = self.name.strip()
        if not name:
            raise WorkflowDefinitionError("output name required")
        object.__setattr__(self, "name", name)


@dataclass(frozen=True, slots=True)
class WorkflowNode:
    """DAG 节点"""

    id: str
    title: str
    instruction: str
    assignee: NodeAssignee
    inputs: Tuple[NodeInput, ...] = ()
    outputs: Tuple[NodeOutput, ...] = ()
    external_capabilities: Tuple[WorkshopToolCapability, ...] = ()
    on_failure: str = "fail_run"

    def __post_init__(self) -> None:
        node_id = self.id.strip()
        title = self.title.strip()
        instruction = self.instruction.strip()
        if not node_id:
            raise WorkflowDefinitionError("node id required")
        if not title:
            raise WorkflowDefinitionError("node title required")
        if not instruction:
            raise WorkflowDefinitionError("node instruction required")
        if self.on_failure not in {"fail_run", "block"}:
            raise WorkflowDefinitionError(
                f"unsupported on_failure: {self.on_failure}"
            )
        object.__setattr__(self, "id", node_id)
        object.__setattr__(self, "title", title)
        object.__setattr__(self, "instruction", instruction)
        object.__setattr__(self, "inputs", tuple(self.inputs))
        object.__setattr__(self, "outputs", tuple(self.outputs))
        object.__setattr__(
            self, "external_capabilities", tuple(self.external_capabilities)
        )


@dataclass(frozen=True, slots=True)
class WorkflowEdge:
    """DAG 依赖边：from 完成后 to 才可跑"""

    from_id: str
    to_id: str

    def __post_init__(self) -> None:
        from_id = self.from_id.strip()
        to_id = self.to_id.strip()
        if not from_id or not to_id:
            raise WorkflowDefinitionError("edge endpoints required")
        if from_id == to_id:
            raise WorkflowDefinitionError("self-loop edge forbidden")
        object.__setattr__(self, "from_id", from_id)
        object.__setattr__(self, "to_id", to_id)


@dataclass(frozen=True, slots=True)
class WorkflowDefinition:
    """待校验的工作流图（不含 project/revision 元数据）"""

    nodes: Tuple[WorkflowNode, ...]
    edges: Tuple[WorkflowEdge, ...]
    known_preset_keys: FrozenSet[str]
    preset_allowlists: Mapping[str, FrozenSet[WorkshopToolCapability]]
    entry_node_ids: Optional[Tuple[str, ...]] = None


def _ancestors(
    node_id: str,
    *,
    incoming: Mapping[str, Tuple[str, ...]],
) -> set[str]:
    """计算严格祖先集合"""
    seen: set[str] = set()
    stack = list(incoming.get(node_id, ()))
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        stack.extend(incoming.get(current, ()))
    return seen


def _has_cycle(node_ids: Sequence[str], edges: Sequence[WorkflowEdge]) -> bool:
    """拓扑检测是否有环"""
    indegree = {node_id: 0 for node_id in node_ids}
    outgoing: dict[str, list[str]] = {node_id: [] for node_id in node_ids}
    for edge in edges:
        outgoing[edge.from_id].append(edge.to_id)
        indegree[edge.to_id] = indegree.get(edge.to_id, 0) + 1
    queue = [node_id for node_id, degree in indegree.items() if degree == 0]
    visited = 0
    while queue:
        current = queue.pop()
        visited += 1
        for nxt in outgoing[current]:
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                queue.append(nxt)
    return visited != len(node_ids)


def validate_workflow_definition(definition: WorkflowDefinition) -> None:
    """保存前校验：结构、环、产物依赖、preset 与能力白名单"""
    if not definition.nodes:
        raise WorkflowDefinitionError("nodes required")

    nodes_by_id: dict[str, WorkflowNode] = {}
    for node in definition.nodes:
        if node.id in nodes_by_id:
            raise WorkflowDefinitionError(f"duplicate node id: {node.id}")
        nodes_by_id[node.id] = node

    for edge in definition.edges:
        if edge.from_id not in nodes_by_id or edge.to_id not in nodes_by_id:
            raise WorkflowDefinitionError("edge endpoint unknown")

    if _has_cycle(tuple(nodes_by_id), definition.edges):
        raise WorkflowDefinitionError("cycle in workflow graph")

    incoming: dict[str, list[str]] = {node_id: [] for node_id in nodes_by_id}
    for edge in definition.edges:
        incoming[edge.to_id].append(edge.from_id)
    incoming_t = {k: tuple(v) for k, v in incoming.items()}

    roots = tuple(
        node_id for node_id, preds in incoming_t.items() if not preds
    )
    if not roots:
        raise WorkflowDefinitionError("entry node required")
    if definition.entry_node_ids is not None:
        entries = tuple(item.strip() for item in definition.entry_node_ids)
        if not entries or any(not item for item in entries):
            raise WorkflowDefinitionError("entry_node_ids invalid")
        if set(entries) != set(roots):
            raise WorkflowDefinitionError("entry_node_ids must match roots")

    output_owners: dict[str, str] = {}
    for node in definition.nodes:
        for output in node.outputs:
            if output.name in output_owners:
                raise WorkflowDefinitionError(
                    f"duplicate artifact name: {output.name}"
                )
            output_owners[output.name] = node.id

        preset = node.assignee.preset_key
        if preset not in definition.known_preset_keys:
            raise WorkflowDefinitionError(f"unknown preset: {preset}")
        allowlist = definition.preset_allowlists.get(preset)
        if allowlist is None:
            raise WorkflowDefinitionError(f"missing allowlist for preset: {preset}")
        for capability in node.external_capabilities:
            if capability not in allowlist:
                raise WorkflowDefinitionError(
                    "external_capabilities outside preset allowlist"
                )

        ancestors = _ancestors(node.id, incoming=incoming_t)
        for inp in node.inputs:
            if inp.kind != "artifact":
                continue
            owner = output_owners.get(inp.name)
            if owner is None:
                # 未预声明端口时须显式 from_node_id，中间名由执行产生
                if inp.from_node_id is None:
                    raise WorkflowDefinitionError(
                        f"unknown artifact input: {inp.name}"
                    )
                if inp.from_node_id not in ancestors:
                    raise WorkflowDefinitionError(
                        f"artifact must come from ancestor: {inp.name}"
                    )
                continue
            if inp.from_node_id is not None and inp.from_node_id != owner:
                raise WorkflowDefinitionError(
                    f"artifact owner mismatch: {inp.name}"
                )
            if owner not in ancestors:
                raise WorkflowDefinitionError(
                    f"artifact must come from ancestor: {inp.name}"
                )


def definition_to_jsonable(
    *,
    nodes: Sequence[WorkflowNode],
    edges: Sequence[WorkflowEdge],
    model_key: str,
    entry_node_ids: Optional[Sequence[str]] = None,
) -> dict[str, object]:
    """序列化为可存 JSONB 的字典"""
    key = model_key.strip()
    if not key:
        raise WorkflowDefinitionError("model_key required")
    payload: dict[str, object] = {
        "model_key": key,
        "nodes": [
            {
                "id": node.id,
                "title": node.title,
                "instruction": node.instruction,
                "assignee": {"preset_key": node.assignee.preset_key},
                "inputs": [
                    {
                        "kind": item.kind,
                        "name": item.name,
                        **(
                            {"from_node_id": item.from_node_id}
                            if item.from_node_id is not None
                            else {}
                        ),
                    }
                    for item in node.inputs
                ],
                "outputs": [
                    {
                        "name": item.name,
                        "storage_type": item.storage_type.value,
                        "required": item.required,
                    }
                    for item in node.outputs
                ],
                "external_capabilities": [
                    cap.value for cap in node.external_capabilities
                ],
                "on_failure": node.on_failure,
            }
            for node in nodes
        ],
        "edges": [
            {"from": edge.from_id, "to": edge.to_id} for edge in edges
        ],
    }
    if entry_node_ids is not None:
        payload["entry_node_ids"] = list(entry_node_ids)
    return payload


def parse_definition_graph(raw: object) -> tuple[
    tuple[WorkflowNode, ...],
    tuple[WorkflowEdge, ...],
    Optional[tuple[str, ...]],
    str,
]:
    """从 JSON 解析 nodes/edges/model_key（不做 preset 目录校验）"""
    if not isinstance(raw, dict):
        raise WorkflowDefinitionError("definition must be object")
    model_key = str(raw.get("model_key", "")).strip()
    if not model_key:
        raise WorkflowDefinitionError("model_key required")
    raw_nodes = raw.get("nodes")
    raw_edges = raw.get("edges")
    if not isinstance(raw_nodes, list) or not raw_nodes:
        raise WorkflowDefinitionError("nodes required")
    if not isinstance(raw_edges, list):
        raise WorkflowDefinitionError("edges required")

    nodes: list[WorkflowNode] = []
    for item in raw_nodes:
        if not isinstance(item, dict):
            raise WorkflowDefinitionError("node must be object")
        assignee_raw = item.get("assignee")
        if not isinstance(assignee_raw, dict):
            raise WorkflowDefinitionError("assignee required")
        inputs_raw = item.get("inputs") or []
        outputs_raw = item.get("outputs") or []
        caps_raw = item.get("external_capabilities") or []
        if not isinstance(inputs_raw, list) or not isinstance(outputs_raw, list):
            raise WorkflowDefinitionError("inputs/outputs must be lists")
        if not isinstance(caps_raw, list):
            raise WorkflowDefinitionError("external_capabilities must be list")
        inputs = tuple(
            NodeInput(
                kind=str(inp.get("kind", "")),
                name=str(inp.get("name", "")),
                from_node_id=(
                    str(inp["from_node_id"])
                    if inp.get("from_node_id") is not None
                    else None
                ),
            )
            for inp in inputs_raw
            if isinstance(inp, dict)
        )
        if len(inputs) != len(inputs_raw):
            raise WorkflowDefinitionError("input must be object")
        outputs = tuple(
            NodeOutput(
                name=str(out.get("name", "")),
                storage_type=WorkshopArtifactStorageType(str(out.get("storage_type"))),
                required=bool(out.get("required", True)),
            )
            for out in outputs_raw
            if isinstance(out, dict)
        )
        if len(outputs) != len(outputs_raw):
            raise WorkflowDefinitionError("output must be object")
        caps = tuple(WorkshopToolCapability(str(cap)) for cap in caps_raw)
        nodes.append(
            WorkflowNode(
                id=str(item.get("id", "")),
                title=str(item.get("title", "")),
                instruction=str(item.get("instruction", "")),
                assignee=NodeAssignee(preset_key=str(assignee_raw.get("preset_key", ""))),
                inputs=inputs,
                outputs=outputs,
                external_capabilities=caps,
                on_failure=str(item.get("on_failure", "fail_run")),
            )
        )

    edges = tuple(
        WorkflowEdge(from_id=str(edge.get("from", "")), to_id=str(edge.get("to", "")))
        for edge in raw_edges
        if isinstance(edge, dict)
    )
    if len(edges) != len(raw_edges):
        raise WorkflowDefinitionError("edge must be object")

    entry_raw = raw.get("entry_node_ids")
    entry: Optional[tuple[str, ...]]
    if entry_raw is None:
        entry = None
    elif isinstance(entry_raw, list):
        entry = tuple(str(item) for item in entry_raw)
    else:
        raise WorkflowDefinitionError("entry_node_ids must be list")
    return tuple(nodes), edges, entry, model_key
