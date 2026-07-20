from __future__ import annotations

from app.contracts.gateway import GatewayVoiceItem
from app.core.gateway import gateway_client
from app.exceptions.base import AppError
from app.exceptions.codes import ErrorCode


async def list_tts_voices(model_id: str) -> list[GatewayVoiceItem]:
    """查询 TTS 模型可用音色。"""
    response = await gateway_client.list_voices(model_id)
    return list(response.data or [])


async def resolve_tts_voice_id(
    model_id: str,
    *,
    voice_id: str | None = None,
    fallback_voice_id: str | None = None,
) -> str:
    """解析 voiceId：显式参数 > 节点已存 > 列表首项。"""
    explicit = (voice_id or fallback_voice_id or "").strip()
    if explicit:
        return explicit
    voices = await list_tts_voices(model_id)
    if not voices:
        raise AppError(ErrorCode.INVALID_PARAMS, "当前 TTS 模型无可用音色")
    first = (voices[0].voice_id or "").strip()
    if not first:
        raise AppError(ErrorCode.INVALID_PARAMS, "当前 TTS 模型无可用音色")
    return first
