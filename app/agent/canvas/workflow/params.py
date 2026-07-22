from __future__ import annotations

from app.agent.runtime.ports import get_generation_port
from app.server.infra.config import settings
from app.server.generation.domain.enums import GenerationKind
from app.server.exceptions.base import AppError
from app.server.exceptions.codes import ErrorCode
from app.server.canvas.persistence.nodes import CanvasNodes
from app.server.generation.services.generation_capabilities import generation_capabilities


async def resolve_node_generation_config(
    node: CanvasNodes,
) -> tuple[str, int | None, bool]:
    """从模型目录补齐节点 model_id 与视频时长, 无可用模型时 fail loud"""
    model_id = (node.model_id or "").strip()
    duration = node.duration_sec
    changed = False

    if not model_id and node.kind in {"image", "video"}:
        resp = await get_generation_port().list_models(node.kind)
        if not resp.items:
            raise AppError(
                ErrorCode.GENERATION_MODEL_CAPABILITY_UNAVAILABLE,
                "没有可用的生成模型，请检查网关与 GENERATION_MODEL_CAPABILITIES 配置",
                {"kind": node.kind},
            )
        model_id = resp.items[0].model_id
        changed = True

    if node.kind == GenerationKind.VIDEO and duration is None and model_id:
        # 视频时长须在模型允许范围内, 否则网关拒绝
        default_duration = int(settings.CANVAS_DEFAULT_VIDEO_DURATION_SEC)
        capabilities = generation_capabilities.get(model_id, GenerationKind.VIDEO)
        if capabilities is None:
            capabilities = await generation_capabilities.require(model_id, GenerationKind.VIDEO)
        allowed = list(capabilities.durations)
        if not allowed or default_duration not in allowed:
            raise AppError(
                ErrorCode.GENERATION_MODEL_CAPABILITY_UNAVAILABLE,
                "Canvas 默认视频时长不在模型能力范围内",
                {"model_id": model_id, "duration": default_duration},
            )
        duration = default_duration
        changed = True

    return model_id, duration, changed
