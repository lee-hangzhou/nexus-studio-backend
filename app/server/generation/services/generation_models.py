from __future__ import annotations

from app.server.infra.cache import app_cache
from app.server.infra.config import settings
from app.server.infra.gateway import gateway_client
from app.server.infra.logger import logger
from app.server.generation.domain.enums import GenerationKind
from app.server.exceptions.base import AppError
from app.server.exceptions.codes import ErrorCode
from app.server.generation.schemas import GenerateModelItem, GenerateModelsResponse
from app.server.generation.services.generation_capabilities import generation_capabilities


async def list_generate_models(kind: str) -> GenerateModelsResponse:
    requested_kind = GenerationKind(kind)

    # 网关模型列表读多写极少：缓存结果，失效纯靠 TTL。网关报错不入缓存（compute 抛出，
    # 由外层捕获后 fail-soft 返回空列表），避免把一次抖动钉死一个 TTL 周期。
    async def _compute() -> dict:
        gateway_models = await gateway_client.list_generation_models()
        items: list[GenerateModelItem] = []
        for gateway_model in gateway_models:
            if gateway_model.generation_kind != requested_kind:
                continue
            capabilities = generation_capabilities.resolve_for_gateway_model(
                gateway_model,
                requested_kind,
            )
            if capabilities is None:
                logger.warning(
                    "generation.capability.unconfigured_model",
                    model_id=gateway_model.id,
                    generation_kind=requested_kind.value,
                )
                continue
            items.append(
                GenerateModelItem(
                    model_id=gateway_model.id,
                    label=gateway_model.id,
                    kind=requested_kind,
                    supports_vision=gateway_model.supports_vision,
                    param_options=generation_capabilities.to_param_options(capabilities),
                )
            )
        return GenerateModelsResponse(items=items).model_dump()

    try:
        raw = await app_cache.get_or_compute(
            f"gen:models:{kind}",
            _compute,
            ttl=settings.GEN_MODEL_LIST_TTL,
            cache_none=False,
        )
    except Exception as exc:
        logger.error("generate.list_models.error", error=str(exc))
        raise AppError(
            ErrorCode.GENERATION_MODEL_LIST_UNAVAILABLE,
            "模型列表暂时不可用",
        ) from exc

    return GenerateModelsResponse(**raw)
