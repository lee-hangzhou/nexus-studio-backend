from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from app.contracts.gateway import (
    GatewayModelItem,
    GatewayModelParameters,
    GatewayModelTaskType,
)
from app.server.exceptions.base import AppError
from app.server.exceptions.codes import ErrorCode
from app.server.generation import assembly
from app.server.generation.domain.constants import MODEL_CAPABILITIES_CACHE_KEY, MODEL_LIST_CACHE_KEY_PREFIX
from app.server.generation.domain.enums import GenerationKind
from app.server.generation.domain.models import GenerationModelCapabilities, MaterialLimits
from app.server.generation.schemas.capabilities_cache import CachedCapabilitiesPayload
from app.server.generation.services.service import GenerationService
from app.server.infra.config import settings


def _image_model(
    model_id: str = "nano-banana-2",
    *,
    reference_modes: list[int] | None = None,
) -> GatewayModelItem:
    return GatewayModelItem(
        id=model_id,
        object="model",
        task_type=GatewayModelTaskType.IMAGE,
        supports_vision=False,
        parameters=GatewayModelParameters(
            resolutions=["2k"],
            ratios=["16:9"],
            counts=[1, 2],
            reference_modes=reference_modes or [],
            material_limits={"images": 8, "videos": 0, "audios": 0},
        ),
    )


def _service(*, gateway: MagicMock, model_cache: MagicMock) -> GenerationService:
    return GenerationService(
        task_repository=MagicMock(),
        asset_repository=MagicMock(),
        attachment_repository=MagicMock(),
        gateway_client=gateway,
        attachment_service=MagicMock(),
        asset_service=MagicMock(),
        object_storage=MagicMock(),
        model_cache=model_cache,
    )


def _caps(model_id: str = "nano-banana-2", *, images: int = 8) -> GenerationModelCapabilities:
    return GenerationModelCapabilities(
        model_id=model_id,
        kind=GenerationKind.IMAGE,
        ratios=("16:9",),
        resolutions=("2k",),
        counts=(1, 2),
        durations=(),
        reference_modes=(),
        material_limits=MaterialLimits(images=images),
    )


def _list_payload(*, images: int = 1) -> dict:
    """Cached list entry may embed stale param_options; serve path rebinds from capabilities."""
    return {
        "items": [
            {
                "model_id": "nano-banana-2",
                "label": "nano-banana-2",
                "kind": "image",
                "supports_vision": False,
                "param_options": {
                    "ratios": ["16:9"],
                    "resolutions": ["2k"],
                    "counts": [1, 2],
                    "durations": [],
                    "reference_modes": [],
                    "ratios_by_resolution": {},
                    "material_limits": {
                        "images": images,
                        "videos": 0,
                        "audios": 0,
                        "requires_any": False,
                        "allow_audio_only": False,
                    },
                },
            }
        ]
    }


def _caps_payload(*, images: int = 8) -> dict:
    return CachedCapabilitiesPayload.from_domain_map(
        {"nano-banana-2": _caps(images=images)},
        ttl_seconds=settings.GEN_MODEL_LIST_TTL,
    ).model_dump(mode="json")


@pytest.mark.asyncio
async def test_list_models_rebinds_param_options_from_shared_capabilities() -> None:
    gateway = MagicMock()
    gateway.list_generation_models = AsyncMock(side_effect=AssertionError("gateway must not run on cache hit"))
    model_cache = MagicMock()

    async def _get_or_compute(key, compute, **_kwargs):
        if key.startswith(MODEL_LIST_CACHE_KEY_PREFIX):
            return _list_payload(images=1)  # stale baked options
        if key == MODEL_CAPABILITIES_CACHE_KEY:
            return _caps_payload(images=8)
        raise AssertionError(key)

    model_cache.get_or_compute = AsyncMock(side_effect=_get_or_compute)
    model_cache.set = AsyncMock()
    service = _service(gateway=gateway, model_cache=model_cache)

    result = await service.list_models(GenerationKind.IMAGE)

    assert result.items[0].param_options.material_limits is not None
    assert result.items[0].param_options.material_limits.images == 8
    gateway.list_generation_models.assert_not_called()
    model_cache.set.assert_not_called()


@pytest.mark.asyncio
async def test_list_models_compute_writes_shared_capabilities_cache() -> None:
    gateway = MagicMock()
    gateway.list_generation_models = AsyncMock(return_value=[_image_model()])
    model_cache = MagicMock()
    model_cache.set = AsyncMock(return_value=True)

    async def _get_or_compute(key, compute, **_kwargs):
        if key.startswith(MODEL_LIST_CACHE_KEY_PREFIX):
            return await compute()
        raise AssertionError(f"unexpected key {key}")

    model_cache.get_or_compute = AsyncMock(side_effect=_get_or_compute)
    service = _service(gateway=gateway, model_cache=model_cache)

    result = await service.list_models(GenerationKind.IMAGE)

    assert result.items[0].model_id == "nano-banana-2"
    assert result.items[0].param_options.material_limits.images == 8
    model_cache.set.assert_awaited()
    assert model_cache.set.await_args.args[0] == MODEL_CAPABILITIES_CACHE_KEY
    assert service.get_model_capabilities("nano-banana-2", GenerationKind.IMAGE) is not None


@pytest.mark.asyncio
async def test_list_models_rejects_malformed_cache_and_recomputes() -> None:
    gateway = MagicMock()
    gateway.list_generation_models = AsyncMock(return_value=[_image_model()])
    model_cache = MagicMock()
    model_cache.set = AsyncMock(return_value=True)
    list_calls = {"n": 0}

    async def _get_or_compute(key, compute, **_kwargs):
        if key.startswith(MODEL_LIST_CACHE_KEY_PREFIX):
            list_calls["n"] += 1
            if list_calls["n"] == 1:
                return {"response": _list_payload(), "capabilities": {}}
            return await compute()
        if key == MODEL_CAPABILITIES_CACHE_KEY:
            return _caps_payload()
        raise AssertionError(key)

    model_cache.get_or_compute = AsyncMock(side_effect=_get_or_compute)
    model_cache.delete = AsyncMock(return_value=True)
    service = _service(gateway=gateway, model_cache=model_cache)

    result = await service.list_models(GenerationKind.IMAGE)

    assert result.items[0].model_id == "nano-banana-2"
    model_cache.delete.assert_awaited()
    assert list_calls["n"] == 2


@pytest.mark.asyncio
async def test_require_uses_memory_when_fresh() -> None:
    gateway = MagicMock()
    gateway.list_generation_models = AsyncMock(return_value=[_image_model()])
    service = _service(gateway=gateway, model_cache=MagicMock())
    payload = CachedCapabilitiesPayload.from_domain_map(
        {"nano-banana-2": _caps()},
        ttl_seconds=settings.GEN_MODEL_LIST_TTL,
    )
    service._install_capabilities_from_payload(payload)

    got = await service.require_model_capabilities("nano-banana-2", GenerationKind.IMAGE)

    assert got.model_id == "nano-banana-2"
    gateway.list_generation_models.assert_not_called()


@pytest.mark.asyncio
async def test_require_loads_shared_capabilities_cache_without_extending_ttl() -> None:
    gateway = MagicMock()
    gateway.list_generation_models = AsyncMock(side_effect=AssertionError("must use shared cache"))
    expires_at = time.time() + 30
    cached = _caps_payload()
    cached["expires_at"] = expires_at
    model_cache = MagicMock()
    model_cache.get_or_compute = AsyncMock(return_value=cached)
    service = _service(gateway=gateway, model_cache=model_cache)

    got = await service.require_model_capabilities("nano-banana-2", GenerationKind.IMAGE)

    assert got.model_id == "nano-banana-2"
    assert service._capabilities_expires_at == expires_at
    model_cache.get_or_compute.assert_awaited_once()
    assert model_cache.get_or_compute.await_args.args[0] == MODEL_CAPABILITIES_CACHE_KEY


@pytest.mark.asyncio
async def test_require_force_refreshes_when_model_missing() -> None:
    gateway = MagicMock()
    gateway.list_generation_models = AsyncMock(return_value=[_image_model("nano-banana-pro")])
    stale = CachedCapabilitiesPayload.from_domain_map(
        {"stale": _caps("stale")},
        ttl_seconds=settings.GEN_MODEL_LIST_TTL,
    )
    model_cache = MagicMock()
    model_cache.get_or_compute = AsyncMock(return_value=stale.model_dump(mode="json"))
    model_cache.set = AsyncMock(return_value=True)
    service = _service(gateway=gateway, model_cache=model_cache)

    got = await service.require_model_capabilities("nano-banana-pro", GenerationKind.IMAGE)

    assert got.model_id == "nano-banana-pro"
    gateway.list_generation_models.assert_awaited_once()
    model_cache.set.assert_awaited()
    assert model_cache.set.await_args.args[0] == MODEL_CAPABILITIES_CACHE_KEY


def test_capabilities_from_gateway_rejects_unknown_reference_mode() -> None:
    with pytest.raises(ValueError, match="unsupported reference_mode"):
        assembly.capabilities_from_gateway_model(_image_model(reference_modes=[99]))


def test_cached_capabilities_payload_rejects_unknown_reference_mode() -> None:
    with pytest.raises(ValidationError):
        CachedCapabilitiesPayload.model_validate(
            {
                "expires_at": time.time() + 10,
                "models": {
                    "bad": {
                        "model_id": "bad",
                        "kind": "image",
                        "ratios": [],
                        "resolutions": [],
                        "counts": [],
                        "durations": [],
                        "reference_modes": [99],
                        "material_limits": {},
                        "ratios_by_resolution": {},
                    }
                },
            }
        )


@pytest.mark.asyncio
async def test_list_models_fails_loud_when_capability_missing_for_kind() -> None:
    gateway = MagicMock()
    gateway.list_generation_models = AsyncMock(return_value=[_image_model("broken")])
    model_cache = MagicMock()
    model_cache.set = AsyncMock(return_value=True)

    async def _get_or_compute(_key, compute, **_kwargs):
        return await compute()

    model_cache.get_or_compute = AsyncMock(side_effect=_get_or_compute)
    service = _service(gateway=gateway, model_cache=model_cache)

    original = assembly.capability_map_from_gateway_models
    try:
        assembly.capability_map_from_gateway_models = lambda _models: {}  # type: ignore[assignment]
        with pytest.raises(AppError) as exc_info:
            await service.list_models(GenerationKind.IMAGE)
    finally:
        assembly.capability_map_from_gateway_models = original  # type: ignore[assignment]

    assert exc_info.value.code == ErrorCode.GENERATION_MODEL_LIST_UNAVAILABLE
