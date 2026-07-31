from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable, Sequence

from app.server.workshop.domain.enums import WorkshopToolCapability

AUTHORIZED_OPS_BRIEF_KEY = "authorized_operations"


@dataclass(frozen=True, slots=True)
class AuthorizedOperation:
    """参数级确认记录（存于 project brief JSON）"""

    id: str
    task_id: str
    capability: WorkshopToolCapability
    operation_kind: str
    payload_hash: str
    granted_by: int
    granted_at: datetime
    consumed_at: datetime | None = None
    revoked_at: datetime | None = None


class AuthorizedOperationError(Exception):
    """授权操作校验失败（fail closed）"""


def stable_payload_hash(payload: dict[str, Any]) -> str:
    """稳定 payload 哈希"""
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _parse_operation(raw: object) -> AuthorizedOperation | None:
    """解析 AuthorizedOperation 记录"""
    if not isinstance(raw, dict):
        return None
    try:
        cap_raw = raw.get("capability")
        cap = (
            cap_raw
            if isinstance(cap_raw, WorkshopToolCapability)
            else WorkshopToolCapability(str(cap_raw))
        )
        granted_at = raw.get("granted_at")
        consumed_at = raw.get("consumed_at")
        revoked_at = raw.get("revoked_at")
        return AuthorizedOperation(
            id=str(raw["id"]),
            task_id=str(raw["task_id"]),
            capability=cap,
            operation_kind=str(raw["operation_kind"]),
            payload_hash=str(raw["payload_hash"]),
            granted_by=int(raw["granted_by"]),
            granted_at=(
                granted_at
                if isinstance(granted_at, datetime)
                else datetime.fromisoformat(str(granted_at))
            ),
            consumed_at=(
                None
                if consumed_at is None
                else (
                    consumed_at
                    if isinstance(consumed_at, datetime)
                    else datetime.fromisoformat(str(consumed_at))
                )
            ),
            revoked_at=(
                None
                if revoked_at is None
                else (
                    revoked_at
                    if isinstance(revoked_at, datetime)
                    else datetime.fromisoformat(str(revoked_at))
                )
            ),
        )
    except (KeyError, TypeError, ValueError):
        return None


def list_authorized_operations(brief: dict[str, Any]) -> tuple[AuthorizedOperation, ...]:
    """从 brief 读取全部授权操作"""
    raw = brief.get(AUTHORIZED_OPS_BRIEF_KEY, [])
    if not isinstance(raw, list):
        return ()
    ops: list[AuthorizedOperation] = []
    for item in raw:
        parsed = _parse_operation(item)
        if parsed is not None:
            ops.append(parsed)
    return tuple(ops)


def append_authorized_operation(
    brief: dict[str, Any],
    operation: AuthorizedOperation,
) -> dict[str, Any]:
    """追加授权操作到 brief"""
    updated = dict(brief)
    existing = list(updated.get(AUTHORIZED_OPS_BRIEF_KEY, []))
    if not isinstance(existing, list):
        existing = []
    existing.append(
        {
            "id": operation.id,
            "task_id": operation.task_id,
            "capability": operation.capability.value,
            "operation_kind": operation.operation_kind,
            "payload_hash": operation.payload_hash,
            "granted_by": operation.granted_by,
            "granted_at": operation.granted_at.isoformat(),
            "consumed_at": (
                operation.consumed_at.isoformat() if operation.consumed_at else None
            ),
            "revoked_at": (
                operation.revoked_at.isoformat() if operation.revoked_at else None
            ),
        }
    )
    updated[AUTHORIZED_OPS_BRIEF_KEY] = existing
    return updated


def authorize_operation(
    *,
    granted_external: Iterable[WorkshopToolCapability],
    authorized_ops: Sequence[AuthorizedOperation],
    capability: WorkshopToolCapability,
    payload_hash: str,
    task_id: str,
) -> AuthorizedOperation:
    """查找匹配且未消费/未撤销的 live AuthorizedOperation；否则 fail closed"""
    if capability not in frozenset(granted_external):
        raise AuthorizedOperationError("task external grant missing for capability")
    for op in authorized_ops:
        if op.task_id != task_id:
            continue
        if op.capability is not capability:
            continue
        if op.payload_hash != payload_hash:
            continue
        if op.revoked_at is not None:
            continue
        if op.consumed_at is not None:
            continue
        return op
    raise AuthorizedOperationError("no matching live authorized operation")


def consume_operation(
    brief: dict[str, Any],
    *,
    operation_id: str,
    consumed_at: datetime,
) -> dict[str, Any]:
    """标记授权操作已消费，返回新 brief"""
    updated = dict(brief)
    raw = updated.get(AUTHORIZED_OPS_BRIEF_KEY, [])
    if not isinstance(raw, list):
        raise AuthorizedOperationError("authorized_operations missing")
    new_list: list[dict[str, Any]] = []
    found = False
    for item in raw:
        if not isinstance(item, dict):
            new_list.append(item)
            continue
        if str(item.get("id")) == operation_id:
            found = True
            patched = dict(item)
            patched["consumed_at"] = consumed_at.isoformat()
            new_list.append(patched)
        else:
            new_list.append(item)
    if not found:
        raise AuthorizedOperationError(f"unknown authorized operation: {operation_id}")
    updated[AUTHORIZED_OPS_BRIEF_KEY] = new_list
    return updated
