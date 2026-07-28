from __future__ import annotations

import logging
from typing import Any
from uuid import UUID, uuid4

from pydantic import TypeAdapter, ValidationError

from app.agent.canvas.tools.canvas_write import llm_node_operation_to_patch_op
from app.agent.canvas.tools.patch_llm_schemas import PatchNodeOperationLlm
from app.agent.runtime.ports import get_canvas_port
from app.agent.runtime.tools.skill_write_pending import build_skill_write_operation
from app.agent.runtime.tools.user_skill_protocol import WRITE_USER_SKILL_FILE
from app.contracts.canvas import (
    CanvasNodeData,
    CanvasNodeView,
    CanvasPosition,
    CreateNodeOp,
    CreateNodePayload,
    PendingCanvasPatchOperation,
    PendingGenerateOperation,
    PendingGenerateSubmitArgs,
    PendingSkillWriteOperation,
    UpdateNodeOp,
    UpdateNodePayload,
)
from app.server.canvas.domain.enums import CanvasNodeKind, CanvasPendingOperationType
from app.server.canvas.domain.node_data import (
    dump_client_writable_node_data,
    merge_client_node_data,
    parse_node_data,
)
from app.server.canvas.schemas.generation import SubmitNodeGenerationInput
from app.server.exceptions.base import AppError

logger = logging.getLogger(__name__)

APPLY_CANVAS_PATCH = "apply_canvas_patch"
SUBMIT_NODE_GENERATION = "submit_node_generation"

_ENRICH_REQUIRED_TOOLS = frozenset({APPLY_CANVAS_PATCH, SUBMIT_NODE_GENERATION, WRITE_USER_SKILL_FILE})


async def enrich_pending_operation(
    name: str,
    args: dict[str, Any] | None,
    *,
    surface: str,
    episode_id: int | None = None,
) -> tuple[dict[str, Any] | None, str]:
    """为 tool_pending 构建结构化 operation；失败返回 (None, failed|skipped)"""
    if name == WRITE_USER_SKILL_FILE:
        op = build_skill_write_operation(name, args, surface=surface)
        if op is None:
            return None, "failed"
        return op, "ok"
    if name == APPLY_CANVAS_PATCH:
        return await _enrich_patch_operation(args, episode_id=episode_id)
    if name == SUBMIT_NODE_GENERATION:
        return _enrich_generate_operation(args)
    return None, "skipped"


async def _enrich_patch_operation(
    args: dict[str, Any] | None,
    *,
    episode_id: int | None,
) -> tuple[dict[str, Any] | None, str]:
    """将 apply_canvas_patch args 充实为 create/update pending"""
    if not isinstance(args, dict):
        return None, "failed"
    raw = args.get("operation")
    if not isinstance(raw, dict):
        return None, "failed"
    try:
        thin = TypeAdapter(PatchNodeOperationLlm).validate_python(raw)
        patch_op = llm_node_operation_to_patch_op(thin)
        pending = await pending_from_node_op(patch_op, episode_id=episode_id)
    except (ValidationError, ValueError, TypeError, AppError) as exc:
        logger.warning("canvas.pending.enrich_failed patch: %s", exc)
        return None, "failed"
    return pending.model_dump(mode="json"), "ok"


async def pending_from_node_op(
    operation: CreateNodeOp | UpdateNodeOp,
    *,
    episode_id: int | None,
) -> PendingCanvasPatchOperation:
    """由已校验的 create/update op 构建 pending 视图"""
    if isinstance(operation, CreateNodeOp):
        node = CanvasNodeView(
            id=uuid4(),
            kind=operation.node.kind,
            revision=1,
            position=operation.node.position,
            width=operation.node.width,
            height=operation.node.height,
            data=operation.node.data,
        )
        return PendingCanvasPatchOperation(
            type=CanvasPendingOperationType.CREATE,
            nodes=[node],
            edges=[],
        )
    if episode_id is None:
        raise ValueError("update pending requires episode_id")
    row = await get_canvas_port().get_node(episode_id, str(operation.node.id))
    if row is None:
        raise ValueError(f"update pending node not found: {operation.node.id}")
    kind = row.kind if isinstance(row.kind, CanvasNodeKind) else CanvasNodeKind(row.kind)
    existing = parse_node_data(row.data)
    if operation.node.data is None:
        merged_data = existing
    else:
        merged_data = merge_client_node_data(existing, operation.node.data, kind=kind)
    node = CanvasNodeView(
        id=operation.node.id,
        kind=kind,
        revision=operation.node.revision,
        position=operation.node.position
        or CanvasPosition(x=float(row.position_x), y=float(row.position_y)),
        width=operation.node.width if operation.node.width is not None else row.width,
        height=operation.node.height if operation.node.height is not None else row.height,
        data=merged_data,
    )
    return PendingCanvasPatchOperation(
        type=CanvasPendingOperationType.UPDATE,
        nodes=[node],
        edges=[],
    )


def _enrich_generate_operation(args: dict[str, Any] | None) -> tuple[dict[str, Any] | None, str]:
    """将 submit_node_generation args 充实为 generate pending"""
    if not isinstance(args, dict):
        return None, "failed"
    try:
        gen = SubmitNodeGenerationInput.model_validate(args)
    except ValidationError as exc:
        logger.warning("canvas.pending.enrich_failed generate: %s", exc)
        return None, "failed"
    try:
        kind = CanvasNodeKind(gen.kind.value if hasattr(gen.kind, "value") else str(gen.kind))
    except ValueError:
        logger.warning("canvas.pending.enrich_failed generate invalid kind=%r", gen.kind)
        return None, "failed"
    if kind not in {
        CanvasNodeKind.IMAGE,
        CanvasNodeKind.VIDEO,
        CanvasNodeKind.AUDIO,
    }:
        logger.warning("canvas.pending.enrich_failed generate unsupported kind=%r", kind)
        return None, "failed"
    config: dict[str, Any] = {"model": gen.model_id}
    if gen.voice_id:
        config["voice_id"] = gen.voice_id
    if gen.ratio:
        config["ratio"] = gen.ratio
    if gen.resolution:
        config["resolution"] = gen.resolution
    if gen.duration is not None:
        config["duration_sec"] = gen.duration
        config["duration"] = str(gen.duration)
    if gen.reference_mode is not None:
        config["reference_mode"] = (
            gen.reference_mode.value
            if hasattr(gen.reference_mode, "value")
            else str(gen.reference_mode)
        )
    if gen.count is not None:
        config["img_num"] = gen.count
    data_payload: dict[str, Any] = {
        "prompt": gen.prompt,
        "model": gen.model_id,
        "config": config,
    }
    try:
        submit_args = PendingGenerateSubmitArgs.model_validate(gen.model_dump(mode="json"))
    except ValidationError as exc:
        logger.warning("canvas.pending.enrich_failed generate submit_args: %s", exc)
        return None, "failed"
    node = CanvasNodeView(
        id=UUID(gen.node_id),
        kind=kind,
        revision=gen.expected_revision or 1,
        position=CanvasPosition(x=0, y=0),
        data=CanvasNodeData.model_validate(data_payload),
    )
    pending = PendingGenerateOperation(
        type=CanvasPendingOperationType.GENERATE,
        node=node,
        submit_args=submit_args,
    )
    return pending.model_dump(mode="json"), "ok"


def edited_tool_args_from_pending_operation(
    tool_name: str,
    raw_operation: dict[str, Any],
) -> dict[str, Any]:
    """将确认卡回传的 pending operation 映射为工具 args"""
    if tool_name == APPLY_CANVAS_PATCH:
        return _edited_patch_args(raw_operation)
    if tool_name == SUBMIT_NODE_GENERATION:
        return _edited_generate_args(raw_operation)
    if tool_name == WRITE_USER_SKILL_FILE:
        return _edited_skill_args(raw_operation)
    raise ValueError(f"unsupported editable pending tool: {tool_name}")


def _edited_patch_args(raw_operation: dict[str, Any]) -> dict[str, Any]:
    """create/update pending → apply_canvas_patch.operation"""
    try:
        pending = PendingCanvasPatchOperation.model_validate(raw_operation)
    except ValidationError as exc:
        raise ValueError(f"invalid pending patch operation: {exc}") from exc
    if len(pending.nodes) != 1:
        raise ValueError("pending create/update requires exactly one node")
    node = pending.nodes[0]
    writable = dump_client_writable_node_data(node.data)
    data = CanvasNodeData.model_validate(writable)
    if pending.type == CanvasPendingOperationType.CREATE:
        payload = CreateNodePayload(
            kind=node.kind,
            position=node.position,
            width=node.width,
            height=node.height,
            data=data,
        )
        return {
            "operation": {
                "op": "create_node",
                "node": payload.model_dump(mode="json", exclude_none=True),
            }
        }
    payload = UpdateNodePayload(
        id=node.id,
        revision=node.revision,
        position=node.position,
        width=node.width,
        height=node.height,
        data=data if writable else None,
    )
    return {
        "operation": {
            "op": "update_node",
            "node": payload.model_dump(mode="json", exclude_none=True),
        }
    }


def _edited_generate_args(raw_operation: dict[str, Any]) -> dict[str, Any]:
    """generate pending → submit_node_generation；以 submit_args 为基，用确认卡可编辑字段覆盖"""
    try:
        pending = PendingGenerateOperation.model_validate(raw_operation)
    except ValidationError as exc:
        raise ValueError(f"invalid pending generate operation: {exc}") from exc
    args = pending.submit_args.model_dump(mode="json")
    data = pending.node.data
    config = data.config
    args["node_id"] = str(pending.node.id)
    args["kind"] = (
        pending.node.kind.value
        if hasattr(pending.node.kind, "value")
        else str(pending.node.kind)
    )
    if data.prompt:
        args["prompt"] = data.prompt
    model = data.model or (config.model if config else None)
    if model:
        args["model_id"] = model
    if not args.get("model_id"):
        raise ValueError("pending generate missing model_id")
    if not args.get("prompt"):
        raise ValueError("pending generate missing prompt")
    if config is None:
        return args
    if config.voice_id:
        args["voice_id"] = config.voice_id
    if config.ratio:
        args["ratio"] = config.ratio
    if config.resolution:
        args["resolution"] = config.resolution
    if config.duration_sec is not None:
        args["duration"] = int(config.duration_sec)
    elif config.duration and str(config.duration).isdigit():
        args["duration"] = int(config.duration)
    if config.reference_mode is not None and "reference_mode" not in args:
        args["reference_mode"] = config.reference_mode
    if config.img_num is not None and "count" not in args:
        args["count"] = int(config.img_num)
    return args


def _edited_skill_args(raw_operation: dict[str, Any]) -> dict[str, Any]:
    """skill_write pending → write_user_skill_file args"""
    try:
        pending = PendingSkillWriteOperation.model_validate(raw_operation)
    except ValidationError as exc:
        raise ValueError(f"invalid pending skill_write operation: {exc}") from exc
    args: dict[str, Any] = {
        "path": pending.path,
        "name": pending.name,
        "content": pending.content,
        "description": pending.description,
    }
    if pending.revision is not None:
        args["revision"] = pending.revision
    return args


def requires_enriched_operation(tool_name: str) -> bool:
    """该工具确认是否必须带结构化 operation"""
    return tool_name in _ENRICH_REQUIRED_TOOLS
