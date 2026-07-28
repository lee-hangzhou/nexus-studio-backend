from __future__ import annotations

import json
import re

from pydantic import BaseModel, Field, ValidationError

from app.server.exceptions.base import AppError
from app.server.exceptions.codes import ErrorCode
from app.server.infra.config import settings
from app.server.infra.gateway import gateway_client
from app.server.infra.logger import logger
from app.server.skills.domain.limits import MAX_DESCRIPTION_LEN


class _SkillDescriptionDecision(BaseModel):
    """网关返回的技能描述决策"""

    description: str = Field(min_length=1, max_length=MAX_DESCRIPTION_LEN)


class _GatewayChoiceMessage(BaseModel):
    content: str | None = None


class _GatewayChoice(BaseModel):
    message: _GatewayChoiceMessage


class _GatewayChatCompletion(BaseModel):
    choices: list[_GatewayChoice] = Field(min_length=1)


def _gateway_model_id() -> str:
    """从配置解析用于描述补全的网关模型 id"""
    model_key = settings.SKILL_DESCRIPTION_FILL_MODEL.strip()
    if not model_key:
        raise AppError(ErrorCode.INVALID_PARAMS, "skill description fill model unavailable")
    try:
        registry = json.loads(settings.CHAT_MODEL_REGISTRY)
    except json.JSONDecodeError as exc:
        raise AppError(ErrorCode.INVALID_PARAMS, "skill description fill model unavailable") from exc
    entry = registry.get(model_key)
    if isinstance(entry, dict) and entry.get("gateway_model"):
        return str(entry["gateway_model"])
    return model_key


def _normalize_description(text: str) -> str:
    """去掉首尾空白并压缩中间空白"""
    return " ".join(text.split())


def _parse_description_decision(response: dict) -> str:
    """从网关 chat completion 响应解析描述"""
    try:
        completion = _GatewayChatCompletion.model_validate(response)
    except ValidationError as exc:
        raise ValueError("empty choices") from exc
    content = completion.choices[0].message.content
    if not isinstance(content, str) or not content.strip():
        raise ValueError("empty content")
    raw = content.strip()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
        if not match:
            raise ValueError("description json missing")
        payload = json.loads(match.group(0))
    decision = _SkillDescriptionDecision.model_validate(payload)
    normalized = _normalize_description(decision.description)
    if not normalized:
        raise ValueError("empty description")
    return normalized[:MAX_DESCRIPTION_LEN]


async def fill_skill_description(*, name: str, content: str) -> str:
    """调用网关补全技能描述, 失败则抛错"""
    gateway_model = _gateway_model_id()
    payload = {
        "model": gateway_model,
        "stream": False,
        "messages": [
            {
                "role": "system",
                "content": (
                    "你是技能摘要助手。根据技能名称与正文, 生成一句中文 description, "
                    f"不超过 {MAX_DESCRIPTION_LEN} 字, 概括用途与适用场景, "
                    "不要复述全文, 不要引号, 不要 markdown。"
                    '只输出 JSON: {"description": "..."}'
                ),
            },
            {
                "role": "user",
                "content": f"名称：{name}\n正文：\n{content[:4000]}",
            },
        ],
    }
    try:
        response = await gateway_client.openai_chat_completion(
            payload,
            timeout_sec=min(settings.CHAT_GATEWAY_TIMEOUT_SEC, 60),
        )
    except AppError:
        raise
    except Exception as exc:
        logger.error("user_skill.description_fill.gateway_failed", error=str(exc))
        raise AppError(ErrorCode.INVALID_PARAMS, "skill description fill failed") from exc

    code = response.get("code")
    if isinstance(code, int) and code not in (0, 200):
        logger.error(
            "user_skill.description_fill.gateway_error",
            code=code,
            message=response.get("message"),
        )
        raise AppError(ErrorCode.INVALID_PARAMS, "skill description fill failed")

    try:
        return _parse_description_decision(response)
    except (ValidationError, ValueError, TypeError, json.JSONDecodeError) as exc:
        logger.error("user_skill.description_fill.parse_failed", error=str(exc))
        raise AppError(ErrorCode.INVALID_PARAMS, "skill description fill failed") from exc
