from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable, Sequence

from app.server.workshop.domain.ecommerce.authorized_operations import (
    AuthorizedOperation,
    AuthorizedOperationError,
    authorize_operation,
)
from app.server.workshop.domain.enums import WorkshopToolCapability

GENERATION_OPERATION_KIND = "generation_submit"


class GenerationGateError(Exception):
    """Generation submit 门禁失败（fail closed）"""


def generation_request_hash(
    *,
    kind: str,
    model: str,
    prompt_summary: str,
    references: Sequence[str],
    count: int,
    parameters: dict[str, Any],
) -> str:
    """Generation submit 请求哈希；参数变化需重确认"""
    payload = {
        "kind": kind,
        "model": model,
        "prompt_summary": prompt_summary,
        "references": sorted(references),
        "count": count,
        "parameters": parameters,
    }
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def assert_generation_submit_allowed(
    *,
    granted_external: Iterable[WorkshopToolCapability],
    authorized_ops: Sequence[AuthorizedOperation],
    request_hash: str,
    task_id: str,
) -> AuthorizedOperation:
    """submit 需 task grant + 匹配 request-hash 的 live AuthorizedOperation"""
    try:
        return authorize_operation(
            granted_external=granted_external,
            authorized_ops=authorized_ops,
            capability=WorkshopToolCapability.GENERATION_SUBMIT,
            payload_hash=request_hash,
            task_id=task_id,
        )
    except AuthorizedOperationError as exc:
        raise GenerationGateError(str(exc)) from exc


def assert_generation_list_allowed(
    *,
    effective_capabilities: Iterable[WorkshopToolCapability],
) -> None:
    """list_models 仅需 profile/role 允许，不需外部授权"""
    if WorkshopToolCapability.GENERATION_LIST_MODELS not in frozenset(effective_capabilities):
        raise GenerationGateError("GENERATION_LIST_MODELS not in effective allowlist")
