from __future__ import annotations

from app.agent.canvas.workflow.inputs import resolve_node_inputs
from app.server.canvas.domain.enums import CanvasNodeKind
from app.server.exceptions.base import AppError
from app.server.exceptions.codes import ErrorCode
from app.server.canvas.persistence.nodes import CanvasNodes


async def resolve_execute_prompt(
    project_id: int,
    node_id: str,
    *,
    prompt_override: str | None = None,
) -> str:
    """解析节点执行 prompt：请求体优先，其次 input_prompt，再上游 dependency。"""
    explicit = (prompt_override or "").strip()
    if explicit:
        return explicit

    row = await CanvasNodes.filter(
        project_id=project_id,
        id=node_id,
        deleted_at__isnull=True,
    ).first()
    if row is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, f"node {node_id} not found")

    if row.input_prompt.strip():
        return row.input_prompt.strip()

    resolved = await resolve_node_inputs(project_id, node_id)
    if resolved.waiting_on:
        reasons = ", ".join(item.reason.value for item in resolved.waiting_on)
        raise AppError(
            ErrorCode.INVALID_PARAMS,
            "节点输入未就绪",
            {"waiting_on": reasons},
        )
    if resolved.local_prompt.strip():
        return resolved.local_prompt.strip()
    if resolved.upstream_texts:
        return "\n\n".join(item.text for item in resolved.upstream_texts)
    raise AppError(ErrorCode.INVALID_PARAMS, "节点缺少可用 prompt")


async def require_node_kind(project_id: int, node_id: str, expected: CanvasNodeKind) -> CanvasNodes:
    """校验节点存在且 kind 匹配。"""
    row = await CanvasNodes.filter(
        project_id=project_id,
        id=node_id,
        deleted_at__isnull=True,
    ).first()
    if row is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, f"node {node_id} not found")
    if row.kind != expected.value:
        raise AppError(
            ErrorCode.INVALID_PARAMS,
            f"node kind mismatch: expected {expected.value}, got {row.kind}",
        )
    return row
