from __future__ import annotations

import json
from typing import Iterable, Sequence

from pydantic import ValidationError

from app.contracts.ecommerce import ECOMMERCE_DELIVERABLE_TYPES, validate_ecommerce_deliverable
from app.server.workshop.domain.enums import (
    WorkshopArtifactStorageType,
    WorkshopToolCapability,
)
from app.server.workshop.domain.external_tool_gate import is_external_capability
from app.server.workshop.domain.types import ArtifactSubmission, WeakAcceptResult


def evaluate_weak_accept(
    *,
    goals: Sequence[str],
    covered_goals: Iterable[str],
    required_artifacts: Sequence[str],
    artifacts: Sequence[ArtifactSubmission],
    granted_external: Iterable[WorkshopToolCapability],
    used_capabilities: Iterable[WorkshopToolCapability],
) -> WeakAcceptResult:
    """按目标覆盖、显式必需产物、电商契约字段与已记录外部能力使用做弱验收判定"""
    reasons: list[str] = []
    covered = frozenset(covered_goals)
    if any(goal not in covered for goal in goals):
        reasons.append("goals_incomplete")

    submitted = {artifact.name.strip(): artifact for artifact in artifacts}
    for required_name in required_artifacts:
        artifact = submitted.get(required_name)
        if artifact is None:
            reasons.append("artifacts_missing")
            break
        if not artifact.is_non_empty():
            reasons.append("artifact_empty")
            break
        if required_name in ECOMMERCE_DELIVERABLE_TYPES:
            if not _ecommerce_artifact_valid(required_name, artifact):
                reasons.append("ecommerce_deliverable_invalid")
                break

    granted = frozenset(granted_external)
    used_external = frozenset(
        capability
        for capability in used_capabilities
        if is_external_capability(capability)
    )
    if not used_external.issubset(granted):
        reasons.append("external_auth_violation")

    if reasons:
        return WeakAcceptResult(passed=False, reasons=tuple(reasons))
    return WeakAcceptResult(passed=True)


def _ecommerce_artifact_valid(type_name: str, artifact: ArtifactSubmission) -> bool:
    """DB 产物 content 必须是可校验的电商交付物 JSON"""
    if artifact.storage_type is not WorkshopArtifactStorageType.DB:
        return False
    if artifact.content is None:
        return False
    try:
        payload = json.loads(artifact.content)
    except json.JSONDecodeError:
        return False
    if not isinstance(payload, dict):
        return False
    try:
        validate_ecommerce_deliverable(type_name, payload)
    except (ValidationError, ValueError):
        return False
    return True
