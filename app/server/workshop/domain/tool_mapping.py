from __future__ import annotations

from typing import FrozenSet

from app.server.workshop.domain.ecommerce.profiles import ExpertProfile, get_ecom_profile, is_ecom_preset
from app.server.workshop.domain.enums import WorkshopToolCapability

_CAPABILITY_TOOL_NAMES: dict[WorkshopToolCapability, str] = {
    WorkshopToolCapability.WEB_SEARCH: "web_search",
    WorkshopToolCapability.WEB_FETCH_READONLY: "web_fetch",
    WorkshopToolCapability.READ_UPLOADS: "read_uploads",
    WorkshopToolCapability.WRITE_TEMP_WORKSPACE: "write_temp_workspace",
    WorkshopToolCapability.READ_PROJECT_FILES: "read_project_files",
    WorkshopToolCapability.WRITE_PROJECT_FILES: "write_project_files",
    WorkshopToolCapability.BROWSER_READ: "browser_read",
    WorkshopToolCapability.BROWSER_WRITE: "browser_write",
    WorkshopToolCapability.SANDBOX_EXECUTE: "sandbox_execute",
    WorkshopToolCapability.MCP: "mcp_invoke",
    WorkshopToolCapability.DRAFT_WORKFLOW: "draft_workflow",
    WorkshopToolCapability.CREATE_SCHEDULE: "create_schedule",
    WorkshopToolCapability.REQUEST_EXTERNAL_AUTH: "request_external_auth",
    WorkshopToolCapability.PROPOSE_INVITE: "propose_invite",
    WorkshopToolCapability.TAOBAO_STORE_WRITE: "taobao_store_write",
    WorkshopToolCapability.GENERATION_LIST_MODELS: "generation_list_models",
    WorkshopToolCapability.GENERATION_SUBMIT: "generation_submit",
}


def capability_tool_names(capabilities: FrozenSet[WorkshopToolCapability]) -> tuple[str, ...]:
    """能力集合 → 稳定排序的工具名快照"""
    names = [_CAPABILITY_TOOL_NAMES[cap] for cap in capabilities if cap in _CAPABILITY_TOOL_NAMES]
    return tuple(sorted(set(names)))


def profile_for_preset(preset_key: str | None) -> ExpertProfile | None:
    """电商 preset 返回 Profile；通用 preset 返回 None"""
    if preset_key is None or not is_ecom_preset(preset_key):
        return None
    return get_ecom_profile(preset_key)
