from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Sequence
from uuid import uuid4

from app.contracts.ecommerce import GenerationAssetRef
from app.server.workshop.domain.ecommerce.authorized_operations import AuthorizedOperation
from app.server.workshop.domain.ecommerce.generation_gate import (
    GenerationGateError,
    assert_generation_list_allowed,
    assert_generation_submit_allowed,
    generation_request_hash,
)
from app.server.workshop.domain.enums import WorkshopToolCapability


@dataclass(frozen=True, slots=True)
class FakeGenerationModel:
    id: str
    kind: str


class FakeWorkshopGenerationProvider:
    """无凭据的假 Generation 提供方，供 workshop 测试使用"""

    def __init__(self, models: Sequence[FakeGenerationModel] | None = None) -> None:
        """初始化"""
        self._models = tuple(
            models
            or (
                FakeGenerationModel(id="fake-image-v1", kind="image"),
                FakeGenerationModel(id="fake-video-v1", kind="video"),
            )
        )
        self.submits: list[dict[str, Any]] = []

    def list_models(self) -> tuple[FakeGenerationModel, ...]:
        """列出可用生成模型"""
        return self._models

    def submit(
        self,
        *,
        kind: str,
        model: str,
        prompt_summary: str,
        count: int,
    ) -> dict[str, Any]:
        """提交生成请求"""
        task_id = f"gen_{uuid4().hex[:12]}"
        record = {
            "generation_task_id": task_id,
            "kind": kind,
            "model": model,
            "prompt_summary": prompt_summary,
            "count": count,
            "storage_key": f"fake://generation/{task_id}",
            "status": "succeeded",
        }
        self.submits.append(record)
        return record


def list_generation_models(
    *,
    effective_capabilities: Sequence[WorkshopToolCapability],
    provider: FakeWorkshopGenerationProvider,
) -> tuple[FakeGenerationModel, ...]:
    """列出生成模型；仅校验 GENERATION_LIST_MODELS，无需外部授权"""
    assert_generation_list_allowed(effective_capabilities=effective_capabilities)
    return provider.list_models()


def submit_generation_with_gates(
    *,
    project_id: str,
    task_id: str,
    expert_id: str,
    sku_id: str,
    kind: str,
    model: str,
    prompt_summary: str,
    references: Sequence[str],
    count: int,
    parameters: dict[str, Any],
    granted_external: Sequence[WorkshopToolCapability],
    authorized_ops: Sequence[AuthorizedOperation],
    provider: FakeWorkshopGenerationProvider,
) -> GenerationAssetRef:
    """提交生成；需 grant 与匹配的 AuthorizedOperation；仅走假 provider"""
    request_hash = generation_request_hash(
        kind=kind,
        model=model,
        prompt_summary=prompt_summary,
        references=references,
        count=count,
        parameters=parameters,
    )
    assert_generation_submit_allowed(
        granted_external=granted_external,
        authorized_ops=authorized_ops,
        request_hash=request_hash,
        task_id=task_id,
    )
    result = provider.submit(
        kind=kind, model=model, prompt_summary=prompt_summary, count=count
    )
    return GenerationAssetRef(
        schema_version="ecom-v1",
        project_id=project_id,
        task_id=task_id,
        expert_id=expert_id,
        created_at=datetime.now(timezone.utc),
        source_refs=[],
        asset_id=f"asset_{uuid4().hex[:12]}",
        sku_id=sku_id,
        generation_task_id=str(result["generation_task_id"]),
        kind=kind,
        model_id=model,
        request_hash=request_hash,
        storage_key=str(result["storage_key"]),
        version=1,
        status="ready",
    )


__all__ = [
    "FakeGenerationModel",
    "FakeWorkshopGenerationProvider",
    "GenerationGateError",
    "list_generation_models",
    "submit_generation_with_gates",
]
