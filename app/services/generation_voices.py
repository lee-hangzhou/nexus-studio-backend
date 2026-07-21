from __future__ import annotations

from app.contracts.gateway import GatewayVoiceItem
from app.core.gateway import gateway_client
from app.exceptions.base import AppError
from app.exceptions.codes import ErrorCode


async def list_tts_voices(model_id: str) -> list[GatewayVoiceItem]:
    """查询 TTS 模型可用音色。"""
    response = await gateway_client.list_voices(model_id)
    return list(response.data)


async def resolve_tts_voice_id(
    model_id: str,
    *,
    voice_id: str | None = None,
    fallback_voice_id: str | None = None,
) -> str:
    """Resolve an explicitly selected or previously persisted voice ID."""
    explicit = (voice_id or fallback_voice_id or "").strip()
    if explicit:
        return explicit
    raise AppError(
        ErrorCode.INVALID_PARAMS,
        "语音生成必须显式选择音色",
        {"model_id": model_id},
    )
