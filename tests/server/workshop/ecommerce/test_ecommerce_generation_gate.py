from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.server.workshop.domain.ecommerce.authorized_operations import AuthorizedOperation
from app.server.workshop.domain.ecommerce.generation_gate import (
    GenerationGateError,
    assert_generation_list_allowed,
    assert_generation_submit_allowed,
    generation_request_hash,
)
from app.server.workshop.domain.enums import WorkshopToolCapability


def test_generation_request_hash_changes_when_parameters_change() -> None:
    """参数变化导致 request hash 变化"""
    base = generation_request_hash(
        kind="image",
        model="model-a",
        prompt_summary="主图",
        references=["asset_1"],
        count=2,
        parameters={"ratio": "1:1"},
    )
    changed = generation_request_hash(
        kind="image",
        model="model-a",
        prompt_summary="主图",
        references=["asset_1"],
        count=3,
        parameters={"ratio": "1:1"},
    )
    assert base != changed


def test_generation_list_allowed_without_external_grant() -> None:
    """list_models 不需外部授权"""
    assert_generation_list_allowed(
        effective_capabilities=frozenset({WorkshopToolCapability.GENERATION_LIST_MODELS})
    )


def test_generation_list_denied_when_not_in_effective_allowlist() -> None:
    """list_models 不在 effective allowlist 时失败"""
    with pytest.raises(GenerationGateError):
        assert_generation_list_allowed(effective_capabilities=frozenset())


def test_generation_submit_denied_without_grant_and_operation() -> None:
    """submit 无 grant / 无 matching op 均失败"""
    request_hash = generation_request_hash(
        kind="image",
        model="m",
        prompt_summary="p",
        references=[],
        count=1,
        parameters={},
    )
    with pytest.raises(GenerationGateError):
        assert_generation_submit_allowed(
            granted_external=frozenset(),
            authorized_ops=(),
            request_hash=request_hash,
            task_id="task_1",
        )
    with pytest.raises(GenerationGateError):
        assert_generation_submit_allowed(
            granted_external=frozenset({WorkshopToolCapability.GENERATION_SUBMIT}),
            authorized_ops=(),
            request_hash=request_hash,
            task_id="task_1",
        )


def test_generation_submit_allowed_with_grant_and_matching_operation() -> None:
    """grant + 匹配 request-hash 的 live op 可 submit"""
    request_hash = generation_request_hash(
        kind="video",
        model="v-model",
        prompt_summary="短视频",
        references=["a1"],
        count=1,
        parameters={"duration": 5},
    )
    op = AuthorizedOperation(
        id="auth_1",
        task_id="task_1",
        capability=WorkshopToolCapability.GENERATION_SUBMIT,
        operation_kind="generation_submit",
        payload_hash=request_hash,
        granted_by=1,
        granted_at=datetime.now(timezone.utc),
    )
    matched = assert_generation_submit_allowed(
        granted_external=frozenset({WorkshopToolCapability.GENERATION_SUBMIT}),
        authorized_ops=(op,),
        request_hash=request_hash,
        task_id="task_1",
    )
    assert matched.id == "auth_1"
