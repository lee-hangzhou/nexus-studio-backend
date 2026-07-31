from __future__ import annotations

from typing import FrozenSet, Sequence

from langchain_core.tools import StructuredTool

from app.server.workshop.domain.tool_mapping import capability_tool_names, profile_for_preset
from app.server.workshop.domain.ecommerce.effective_profile import (
    expert_kind_to_role,
    resolve_effective_capabilities,
)
from app.server.workshop.domain.ecommerce.profiles import get_ecom_profile, is_ecom_preset
from app.server.workshop.domain.enums import WorkshopRole, WorkshopToolCapability
from app.server.workshop.domain.expert_catalog import get_catalog_entry
from app.server.workshop.domain.presets import get_preset
from app.server.workshop.domain.role_policy import capabilities_for


def profile_allowlist_for_key(expert_key: str) -> FrozenSet[WorkshopToolCapability]:
    """读取专家 preset 的能力白名单"""
    if is_ecom_preset(expert_key):
        return get_ecom_profile(expert_key).capability_allowlist
    preset = get_preset(expert_key)
    role = expert_kind_to_role(preset.kind)
    return capabilities_for(role)


def profile_tool_names_for_chat(expert_key: str) -> frozenset[str]:
    """Chat 单 Agent：SINGLE_AGENT 天花板 ∩ profile 能力 → 工具名"""
    chat_ceiling = capabilities_for(WorkshopRole.SINGLE_AGENT)
    profile_allowlist = profile_allowlist_for_key(expert_key)
    effective = resolve_effective_capabilities(
        WorkshopRole.SINGLE_AGENT,
        profile_allowlist & chat_ceiling,
        frozenset(),
    )
    return frozenset(capability_tool_names(effective))


def profile_tool_names_for_workshop_expert(
    expert_key: str,
    *,
    granted_external: FrozenSet[WorkshopToolCapability] = frozenset(),
) -> frozenset[str]:
    """Workshop 专家回合：角色天花板 ∩ profile"""
    profile = profile_for_preset(expert_key)
    if profile is not None:
        role = expert_kind_to_role(profile.kind)
        effective = resolve_effective_capabilities(
            role,
            profile.capability_allowlist,
            granted_external,
        )
        return frozenset(capability_tool_names(effective))
    preset = get_preset(expert_key)
    role = expert_kind_to_role(preset.kind)
    effective = resolve_effective_capabilities(
        role,
        capabilities_for(role),
        granted_external,
    )
    return frozenset(capability_tool_names(effective))


def intersect_chat_tools_with_profile(
    tools: Sequence[StructuredTool],
    allowed_names: frozenset[str],
) -> list[StructuredTool]:
    """按允许工具名过滤 Chat 工具列表"""
    return [tool for tool in tools if tool.name in allowed_names]


def build_expert_identity_block(expert_key: str) -> str:
    """组装专家身份与边界 system 块"""
    catalog = get_catalog_entry(expert_key)
    lines = [
        "## 当前专家身份",
        f"你是「{catalog.name}」，职责：{catalog.role_phrase}。",
        f"标签：{', '.join(catalog.tags)}。",
    ]
    if is_ecom_preset(expert_key):
        profile = get_ecom_profile(expert_key)
        boundary = profile.system_prompt_boundary.strip()
        if boundary:
            lines.append(f"边界：{boundary}")
    lines.append(f"仅以「{catalog.name}」身份发言，不得冒充其他专家或主持角色。")
    lines.append("仍以 Chat 安全策略为准，不得越权调用未开放工具。")
    return "\n".join(lines)
