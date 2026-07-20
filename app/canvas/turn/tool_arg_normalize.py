from __future__ import annotations

import json
from typing import Any


def _pop_project_id(args: dict[str, Any]) -> dict[str, Any]:
    """工具已绑定 project_id, 忽略模型传入的 project_id"""
    out = dict(args)
    out.pop("project_id", None)
    return out


def _flat_create_node_payload(raw: dict[str, Any]) -> dict[str, Any]:
    """把扁平 create_node 参数整理为 node 字段"""
    node: dict[str, Any] = {}
    if isinstance(raw.get("node"), dict):
        return dict(raw["node"])

    for key in (
        "kind",
        "input_prompt",
        "output_text",
        "status",
        "model_id",
        "ratio",
        "resolution",
        "output_asset_ids",
    ):
        if key in raw:
            node[key] = raw[key]

    title = raw.get("title") or raw.get("label")
    if title is not None:
        node["title"] = str(title)

    position = raw.get("position")
    if not isinstance(position, dict):
        x = raw.get("x")
        y = raw.get("y")
        if x is not None or y is not None:
            position = {"x": float(x if x is not None else 0), "y": float(y if y is not None else 0)}
    if isinstance(position, dict):
        node["position"] = position

    duration = raw.get("duration_sec", raw.get("duration"))
    if duration is not None:
        node["duration_sec"] = int(duration)

    return node


def _coerce_patch_op(raw: Any) -> dict[str, Any]:
    """只应用已声明字段别名, 不猜测缺失操作类型"""
    if not isinstance(raw, dict):
        return {"invalid_operation": raw}

    op_name = str(raw.get("op") or raw.get("operation") or "")
    if not op_name:
        return dict(raw)

    if op_name == "create_node":
        node = _flat_create_node_payload(raw)
        return {"op": "create_node", "node": node}

    out: dict[str, Any] = {"op": op_name}
    for key in ("node_id", "patch", "edge", "edge_id"):
        if key in raw:
            out[key] = raw[key]
    return out


def _normalize_apply_canvas_patch(args: dict[str, Any]) -> dict[str, Any]:
    """规范化 apply_canvas_patch 工具参数"""
    out = _pop_project_id(args)

    for alias, target in (("operations", "ops"), ("operation_list", "ops")):
        if alias in out and target not in out:
            out[target] = out.pop(alias)

    expected_revision = out.get("expected_revision")
    ops = out.get("ops")
    if isinstance(ops, str):
        try:
            ops = json.loads(ops)
        except json.JSONDecodeError:
            ops = None
    if isinstance(ops, list):
        ops = [_coerce_patch_op(item) for item in ops]
    elif isinstance(ops, dict):
        ops = [_coerce_patch_op(ops)]

    normalized: dict[str, Any] = {}
    if expected_revision is not None:
        normalized["expected_revision"] = expected_revision
    if ops:
        normalized["ops"] = ops
    return normalized


def _normalize_submit_node_generation(args: dict[str, Any]) -> dict[str, Any]:
    """规范化 submit_node_generation 工具参数"""
    out = _pop_project_id(args)

    if "duration_sec" in out and "duration" not in out:
        out["duration"] = out.pop("duration_sec")

    return out


def normalize_canvas_tool_args(tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
    """按工具名分派参数规范化逻辑"""
    if not args:
        return args
    if tool_name == "apply_canvas_patch":
        return _normalize_apply_canvas_patch(args)
    if tool_name == "submit_node_generation":
        return _normalize_submit_node_generation(args)
    return _pop_project_id(args)
