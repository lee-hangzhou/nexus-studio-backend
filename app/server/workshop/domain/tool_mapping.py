from __future__ import annotations

from typing import FrozenSet

from app.server.workshop.domain.ecommerce.profiles import ExpertProfile, get_ecom_profile, is_ecom_preset
from app.server.workshop.domain.enums import WorkshopToolCapability

# 能力 → 实际可挂载工具名（Chat / Host）。一对多；未落地的能力保留逻辑名作快照身份。
_CAPABILITY_TOOL_NAMES: dict[WorkshopToolCapability, tuple[str, ...]] = {
    WorkshopToolCapability.WEB_SEARCH: ("web_search",),
    WorkshopToolCapability.WEB_FETCH_READONLY: ("web_fetch",),
    WorkshopToolCapability.READ_UPLOADS: ("read_file", "list_files"),
    WorkshopToolCapability.WRITE_TEMP_WORKSPACE: ("write_file",),
    WorkshopToolCapability.READ_PROJECT_FILES: ("read_file", "list_files"),
    WorkshopToolCapability.WRITE_PROJECT_FILES: ("write_file", "publish_file"),
    WorkshopToolCapability.BROWSER_READ: (
        "browser_capture_state",
        "browser_auth_status",
        "signal_browser_blocked",
        "browser_challenge_read_geometry",
        "browser_challenge_screenshot_element",
        "browser_challenge_wait_probe",
        "cv_image_info",
        "cv_match_template",
        "cv_find_gap_x",
        "browser_restore_session",
        "browser_end_session",
    ),
    WorkshopToolCapability.BROWSER_WRITE: (
        "browser_exec_script",
        "request_user_gate",
        "browser_trigger_otp_send",
        "browser_challenge_dispatch_pointer_trace",
    ),
    WorkshopToolCapability.SANDBOX_EXECUTE: ("execute_python",),
    WorkshopToolCapability.MCP: ("mcp_invoke",),
    WorkshopToolCapability.DRAFT_WORKFLOW: ("draft_workflow",),
    WorkshopToolCapability.CONFIRM_SAVE_WORKFLOW: ("confirm_save_workflow",),
    WorkshopToolCapability.CREATE_SCHEDULE: ("create_schedule",),
    WorkshopToolCapability.MANUAL_RUN_WORKFLOW: ("manual_run_workflow",),
    WorkshopToolCapability.REQUEST_EXTERNAL_AUTH: ("request_external_auth",),
    WorkshopToolCapability.PROPOSE_INVITE: ("propose_upgrade_and_invite",),
    WorkshopToolCapability.INVITE_EXPERT: ("invite_experts",),
    WorkshopToolCapability.TAOBAO_STORE_WRITE: ("taobao_store_write",),
    WorkshopToolCapability.GENERATION_LIST_MODELS: ("generation_list_models",),
    WorkshopToolCapability.GENERATION_SUBMIT: ("generation_submit",),
}


def capability_tool_names(capabilities: FrozenSet[WorkshopToolCapability]) -> tuple[str, ...]:
    """能力集合 → 稳定排序的实际工具名快照"""
    names: set[str] = set()
    for cap in capabilities:
        mapped = _CAPABILITY_TOOL_NAMES.get(cap)
        if mapped is None:
            continue
        names.update(mapped)
    return tuple(sorted(names))


def profile_for_preset(preset_key: str | None) -> ExpertProfile | None:
    """电商 preset 返回 Profile；通用 preset 返回 None"""
    if preset_key is None or not is_ecom_preset(preset_key):
        return None
    return get_ecom_profile(preset_key)
