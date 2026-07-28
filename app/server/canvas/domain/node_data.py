"""canvas_nodes.data 合并与投影辅助

字段语义对齐参考（storyflow / canvas-agent）：
- text：content=正文(str)；prompt_content=生成输入(segments)；prompt=由 prompt_content 派生的纯文本
- media：content=结构化 prompt(segments)；prompt=派生纯文本；prompt_content 非主路径
"""

from __future__ import annotations

from typing import Any

from app.contracts.canvas import (
    NODE_DATA_CLIENT_FORBIDDEN_KEYS,
    CanvasNodeConfig,
    CanvasNodeData,
    CanvasNodeKind,
    WorkflowPromptContentSegment,
    WorkflowPromptTextSegment,
    validate_node_data_content,
)
from app.server.canvas.domain.enums import CanvasNodeStatus
from app.server.exceptions.base import AppError
from app.server.exceptions.codes import ErrorCode


def empty_node_data(*, status: CanvasNodeStatus = CanvasNodeStatus.IDLE) -> dict[str, Any]:
    """返回带默认 status 的空 data dict（omit None）"""
    return CanvasNodeData(status=status).model_dump(mode="json", exclude_none=True)


def parse_node_data(raw: Any) -> CanvasNodeData:
    """将持久化 JSON 解析为 CanvasNodeData"""
    if raw is None:
        return CanvasNodeData(status=CanvasNodeStatus.IDLE)
    if isinstance(raw, CanvasNodeData):
        return raw
    if not isinstance(raw, dict):
        raise AppError(ErrorCode.INVALID_PARAMS, "node data must be object")
    return CanvasNodeData.model_validate(raw)


def dump_node_data(data: CanvasNodeData) -> dict[str, Any]:
    """序列化节点 data 写入 JSONB"""
    return data.model_dump(mode="json", exclude_none=False)


def dump_client_writable_node_data(data: CanvasNodeData) -> dict[str, Any]:
    """从已校验的 CanvasNodeData 剥成客户端可写子集（去投影字段与 None）"""
    payload = data.model_dump(mode="json", exclude_none=True)
    for key in NODE_DATA_CLIENT_FORBIDDEN_KEYS:
        payload.pop(key, None)
    return payload


def merge_client_node_data(
    existing: CanvasNodeData,
    patch: CanvasNodeData,
    *,
    kind: CanvasNodeKind | str,
) -> CanvasNodeData:
    """白名单 merge：仅合并 patch.model_fields_set，拒绝投影字段，并按 kind 校验 content"""
    forbidden = NODE_DATA_CLIENT_FORBIDDEN_KEYS.intersection(patch.model_fields_set)
    if forbidden:
        raise AppError(
            ErrorCode.INVALID_PARAMS,
            f"client cannot write node data fields: {sorted(forbidden)}",
        )
    base = existing.model_dump(mode="python")
    incoming = patch.model_dump(mode="python", exclude_unset=True)
    if "config" in incoming and incoming["config"] is not None:
        existing_config = existing.config.model_dump(mode="python") if existing.config else {}
        patch_config = (
            patch.config.model_dump(mode="python", exclude_unset=True) if patch.config else {}
        )
        incoming["config"] = {**existing_config, **patch_config}
    base.update(incoming)
    merged = CanvasNodeData.model_validate(base)
    try:
        validate_node_data_content(kind, merged)
    except ValueError as exc:
        raise AppError(ErrorCode.INVALID_PARAMS, str(exc)) from exc
    return merged


def apply_generation_to_data(
    existing: CanvasNodeData,
    *,
    status: CanvasNodeStatus,
    generate_task_id: int | None = None,
    generate_error: str | None = None,
    output_asset_ids: list[int] | None = None,
    model: str | None = None,
    voice_id: str | None = None,
    duration_sec: int | None = None,
    ratio: str | None = None,
    resolution: str | None = None,
    set_task_id: bool = True,
    set_error: bool = True,
) -> CanvasNodeData:
    """系统投影写回生成态字段"""
    payload = existing.model_dump(mode="python")
    payload["status"] = status
    if set_task_id:
        payload["generate_task_id"] = generate_task_id
    if set_error:
        payload["generate_error"] = generate_error
    if output_asset_ids is not None:
        payload["output_asset_ids"] = output_asset_ids
    config = dict(payload.get("config") or {})
    if model is not None:
        payload["model"] = model
        config["model"] = model
    if voice_id is not None:
        config["voice_id"] = voice_id
    if duration_sec is not None:
        config["duration_sec"] = duration_sec
        config["duration"] = str(duration_sec)
    if ratio is not None:
        config["ratio"] = ratio
    if resolution is not None:
        config["resolution"] = resolution
    payload["config"] = config or None
    return CanvasNodeData.model_validate(payload)


def data_status(data: CanvasNodeData | dict[str, Any] | None) -> CanvasNodeStatus:
    """读取 data.status，缺省 idle"""
    if data is None:
        return CanvasNodeStatus.IDLE
    if isinstance(data, dict):
        raw = data.get("status")
    else:
        raw = data.status
    if raw is None:
        return CanvasNodeStatus.IDLE
    return CanvasNodeStatus(raw)


def _segment_texts(segments: list[Any] | None) -> str:
    if not segments:
        return ""
    parts: list[str] = []
    for seg in segments:
        seg_type = seg.type if hasattr(seg, "type") else seg.get("type")  # type: ignore[union-attr]
        if seg_type != "text":
            continue
        text = seg.text if hasattr(seg, "text") else seg.get("text")  # type: ignore[union-attr]
        if text:
            parts.append(str(text))
    return "\n".join(parts)


def data_prompt_text(data: CanvasNodeData, kind: CanvasNodeKind | str) -> str:
    """生成用纯文本：prompt 优先，再 prompt_content；media 可回退 content segments

    text 的 content 是正文，不作为 prompt 回退
    """
    if data.prompt and data.prompt.strip():
        return data.prompt.strip()
    from_prompt_content = _segment_texts(data.prompt_content)
    if from_prompt_content.strip():
        return from_prompt_content.strip()
    kind_value = kind.value if isinstance(kind, CanvasNodeKind) else str(kind)
    if kind_value == CanvasNodeKind.TEXT:
        return ""
    if isinstance(data.content, list):
        return _segment_texts(data.content).strip()
    if isinstance(data.content, str) and data.content.strip():
        return data.content.strip()
    return ""


def data_output_text(data: CanvasNodeData) -> str:
    """text 节点正文（content 字符串）；非字符串返回空"""
    return data.content if isinstance(data.content, str) else ""


def data_model_id(data: CanvasNodeData) -> str | None:
    """读取 model / config.model"""
    if data.model:
        return data.model
    if data.config and data.config.model:
        return data.config.model
    return None


def data_voice_id(data: CanvasNodeData) -> str | None:
    """读取 config.voice_id"""
    if data.config and data.config.voice_id:
        return data.config.voice_id
    return None


def data_ratio(data: CanvasNodeData) -> str | None:
    """读取 config.ratio"""
    if data.config and data.config.ratio:
        return data.config.ratio
    return None


def data_resolution(data: CanvasNodeData) -> str | None:
    """读取 config.resolution"""
    if data.config and data.config.resolution:
        return data.config.resolution
    return None


def data_duration_sec(data: CanvasNodeData) -> int | None:
    """读取 config.duration_sec"""
    if data.config and data.config.duration_sec is not None:
        try:
            return int(data.config.duration_sec)
        except (TypeError, ValueError):
            return None
    return None


def data_task_id(data: CanvasNodeData) -> int | None:
    """读取 generate_task_id"""
    if data.generate_task_id is None:
        return None
    try:
        return int(data.generate_task_id)
    except (TypeError, ValueError):
        return None


def data_output_asset_ids(data: CanvasNodeData) -> tuple[int, ...]:
    """读取 output_asset_ids"""
    raw = data.output_asset_ids
    if not isinstance(raw, list):
        return ()
    return tuple(int(item) for item in raw)


def data_generate_error(data: CanvasNodeData) -> str | None:
    """读取 generate_error"""
    return data.generate_error or None


def ensure_create_defaults(kind: CanvasNodeKind | str, data: CanvasNodeData) -> CanvasNodeData:
    """创建时补齐默认 status / text content"""
    payload = data.model_dump(mode="python", exclude_unset=False)
    if payload.get("status") is None:
        payload["status"] = CanvasNodeStatus.IDLE
    kind_value = kind.value if isinstance(kind, CanvasNodeKind) else str(kind)
    if kind_value == CanvasNodeKind.TEXT and payload.get("content") is None:
        payload["content"] = ""
    return CanvasNodeData.model_validate(payload)


def sync_text_prompt_fields(prompt: str) -> dict[str, Any]:
    """text 生成输入：同步 prompt + prompt_content，不碰 content 正文"""
    trimmed = prompt.strip()
    return {
        "prompt": prompt,
        "prompt_content": (
            [WorkflowPromptTextSegment(type="text", text=trimmed)] if trimmed else []
        ),
    }


def sync_media_prompt_fields(
    prompt: str,
    *,
    content: list[WorkflowPromptContentSegment] | None = None,
) -> dict[str, Any]:
    """media 生成输入：写 prompt；无富 content 时用 text segment 占位"""
    trimmed = prompt.strip()
    if content is not None:
        return {"prompt": prompt, "content": content}
    return {
        "prompt": prompt,
        "content": (
            [WorkflowPromptTextSegment(type="text", text=trimmed)] if trimmed else None
        ),
    }


def config_from_flat(
    *,
    model_id: str | None = None,
    voice_id: str | None = None,
    ratio: str | None = None,
    resolution: str | None = None,
    duration_sec: int | None = None,
) -> CanvasNodeConfig | None:
    """由扁平配置字段组装 CanvasNodeConfig"""
    cfg = CanvasNodeConfig(
        model=model_id,
        voice_id=voice_id,
        ratio=ratio,
        resolution=resolution,
        duration_sec=duration_sec,
        duration=str(duration_sec) if duration_sec is not None else None,
    )
    if not any(cfg.model_dump(exclude_none=True).values()):
        return None
    return cfg
