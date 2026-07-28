import json
from dataclasses import dataclass
from typing import Any, Dict, List

from app.agent.chat.llm.model_catalog import MODEL_CAPABILITY_UNAVAILABLE, model_catalog, parse_chat_model_rows
from app.agent.chat.llm.thinking import ThinkingConfig, thinking_config_from_registry
from app.server.infra.cache import app_cache
from app.server.infra.config import settings
from app.server.infra.gateway import gateway_client
from app.server.exceptions.base import AppError
from app.server.exceptions.codes import ErrorCode


@dataclass(frozen=True)
class ModelSpec:
    family: str
    gateway_model: str
    display_name: str
    context_budget: int
    thinking: ThinkingConfig
    supports_vision: bool


def _base_spec(model_key: str, entry: dict[str, Any] | None) -> ModelSpec:
    if entry is not None:
        return ModelSpec(
            family=str(entry["family"]),
            gateway_model=str(entry["gateway_model"]),
            display_name=str(entry.get("display_name", model_key)),
            context_budget=int(entry.get("context_budget", settings.CHAT_CONTEXT_BUDGET)),
            thinking=thinking_config_from_registry(entry.get("thinking")),
            supports_vision=model_catalog.require_supports_vision(model_key),
        )
    return ModelSpec(
        family="openai",
        gateway_model=model_key,
        display_name=model_key,
        context_budget=settings.CHAT_CONTEXT_BUDGET,
        thinking=thinking_config_from_registry(None),
        supports_vision=model_catalog.require_supports_vision(model_key),
    )


def load_model_registry() -> Dict[str, ModelSpec]:
    raw = json.loads(settings.CHAT_MODEL_REGISTRY)
    registry: Dict[str, ModelSpec] = {}
    for key, value in raw.items():
        registry[key] = _base_spec(key, value)
    return registry


def get_model_spec(model_key: str) -> ModelSpec:
    raw = json.loads(settings.CHAT_MODEL_REGISTRY)
    entry = raw.get(model_key)
    if isinstance(entry, dict):
        return _base_spec(model_key, entry)
    return _base_spec(model_key, None)


async def list_models_for_api() -> List[Dict[str, Any]]:
    # 网关模型列表读多写极少：缓存结果，失效纯靠 TTL。compute 内仍刷新 model_catalog。
    return await app_cache.get_or_compute(
        "chat:models",
        _compute_models_for_api,
        ttl=settings.CHAT_MODEL_LIST_TTL,
        cache_none=False,
    )


async def _compute_models_for_api() -> List[Dict[str, Any]]:
    try:
        payload = await gateway_client.list_openai_models()
    except Exception as exc:
        raise AppError(ErrorCode.INVALID_PARAMS, MODEL_CAPABILITY_UNAVAILABLE) from exc

    rows = parse_chat_model_rows(payload)
    if not rows:
        raise AppError(ErrorCode.INVALID_PARAMS, MODEL_CAPABILITY_UNAVAILABLE)

    model_catalog.update_from_gateway_rows(rows)

    result: List[Dict[str, Any]] = []
    for row in rows:
        if row.get("task_type") != 1:
            continue
        model_id = row.get("id")
        if not isinstance(model_id, str) or not model_id:
            continue
        supports_vision = row.get("supports_vision")
        supports_video_input = row.get("supports_video_input")
        if not isinstance(supports_vision, bool) or not isinstance(supports_video_input, bool):
            raise AppError(ErrorCode.INVALID_PARAMS, MODEL_CAPABILITY_UNAVAILABLE)

        spec = get_model_spec(model_id)
        result.append(
            {
                "key": model_id,
                "display_name": model_id,
                "family": spec.family,
                "supports_vision": supports_vision,
            }
        )

    if not result:
        raise AppError(ErrorCode.INVALID_PARAMS, MODEL_CAPABILITY_UNAVAILABLE)
    return result
