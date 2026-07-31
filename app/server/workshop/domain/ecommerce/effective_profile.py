from __future__ import annotations

from typing import FrozenSet

from app.server.workshop.domain.enums import WorkshopExpertKind, WorkshopRole, WorkshopToolCapability
from app.server.workshop.domain.external_tool_gate import is_external_capability
from app.server.workshop.domain.role_policy import capabilities_for


def resolve_effective_capabilities(
    role: WorkshopRole,
    profile_allowlist: FrozenSet[WorkshopToolCapability],
    granted_external: FrozenSet[WorkshopToolCapability],
) -> frozenset[WorkshopToolCapability]:
    """求 role_ceiling ∩ profile ∩（非外部常开 | 外部需 grant）"""
    role_ceiling = capabilities_for(role)
    base = role_ceiling & profile_allowlist
    effective: set[WorkshopToolCapability] = set()
    for cap in base:
        if is_external_capability(cap):
            if cap in granted_external:
                effective.add(cap)
        else:
            effective.add(cap)
    return frozenset(effective)


def expert_kind_to_role(kind: WorkshopExpertKind) -> WorkshopRole:
    """专家岗位类型映射到角色天花板"""
    if kind is WorkshopExpertKind.ADVISOR:
        return WorkshopRole.ADVISOR
    return WorkshopRole.EXECUTOR


def workshop_thread_id(project_id: str, expert_id: str, task_id: str) -> str:
    """专家×任务 checkpointer 键"""
    return f"workshop:{project_id}:expert:{expert_id}:task:{task_id}"


def workshop_expert_idle_thread_id(project_id: str, expert_id: str) -> str:
    """专家无任务闲聊 checkpointer 键（与 Host idle 隔离）"""
    return f"workshop:{project_id}:expert:{expert_id}:idle"


def workshop_host_idle_thread_id(project_id: str) -> str:
    """Host idle checkpointer 键"""
    return f"workshop:{project_id}:host:idle"
