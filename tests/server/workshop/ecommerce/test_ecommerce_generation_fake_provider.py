from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.server.workshop.domain.ecommerce.authorized_operations import AuthorizedOperation
from app.server.workshop.domain.ecommerce.generation_gate import (
    GenerationGateError,
    generation_request_hash,
)
from app.server.workshop.domain.ecommerce.generation_service import (
    FakeWorkshopGenerationProvider,
    list_generation_models,
    submit_generation_with_gates,
)
from app.server.workshop.domain.enums import WorkshopToolCapability


def test_fake_list_models_without_external_auth() -> None:
    """覆盖 fake list models without external auth"""
    provider = FakeWorkshopGenerationProvider()
    models = list_generation_models(
        effective_capabilities=(WorkshopToolCapability.GENERATION_LIST_MODELS,),
        provider=provider,
    )
    assert len(models) >= 1


def test_fake_submit_requires_grant_and_hash_then_returns_asset_ref() -> None:
    """覆盖 fake submit requires grant and hash then returns asset ref"""
    provider = FakeWorkshopGenerationProvider()
    request_hash = generation_request_hash(
        kind="image",
        model="fake-image-v1",
        prompt_summary="主图",
        references=["ref1"],
        count=1,
        parameters={"ratio": "1:1"},
    )
    op = AuthorizedOperation(
        id="aop_1",
        task_id="task_1",
        capability=WorkshopToolCapability.GENERATION_SUBMIT,
        operation_kind="generation_submit",
        payload_hash=request_hash,
        granted_by=1,
        granted_at=datetime.now(timezone.utc),
    )
    asset = submit_generation_with_gates(
        project_id="wp_1",
        task_id="task_1",
        expert_id="ecom_listing_planner_executor",
        sku_id="sku_1",
        kind="image",
        model="fake-image-v1",
        prompt_summary="主图",
        references=["ref1"],
        count=1,
        parameters={"ratio": "1:1"},
        granted_external=(WorkshopToolCapability.GENERATION_SUBMIT,),
        authorized_ops=(op,),
        provider=provider,
    )
    assert asset.asset_id
    assert asset.request_hash == request_hash
    assert asset.status == "ready"
    assert len(provider.submits) == 1


def test_fake_submit_fails_without_authorized_operation() -> None:
    """覆盖 fake submit fails without authorized operation"""
    provider = FakeWorkshopGenerationProvider()
    with pytest.raises(GenerationGateError):
        submit_generation_with_gates(
            project_id="wp_1",
            task_id="task_1",
            expert_id="ecom_listing_planner_executor",
            sku_id="sku_1",
            kind="image",
            model="fake-image-v1",
            prompt_summary="主图",
            references=[],
            count=1,
            parameters={},
            granted_external=(WorkshopToolCapability.GENERATION_SUBMIT,),
            authorized_ops=(),
            provider=provider,
        )
