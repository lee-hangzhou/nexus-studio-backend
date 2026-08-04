from __future__ import annotations

from typing import FrozenSet

from app.server.workshop.domain.enums import WorkshopRole, WorkshopToolCapability

_SINGLE_AGENT: FrozenSet[WorkshopToolCapability] = frozenset(
    {
        WorkshopToolCapability.WEB_SEARCH,
        WorkshopToolCapability.WEB_FETCH_READONLY,
        WorkshopToolCapability.READ_UPLOADS,
        WorkshopToolCapability.WRITE_TEMP_WORKSPACE,
    }
)

_HOST: FrozenSet[WorkshopToolCapability] = frozenset(
    {
        WorkshopToolCapability.READ_PROJECT_FILES,
        WorkshopToolCapability.INVITE_EXPERT,
        WorkshopToolCapability.RAISE_AUTH_POPUP,
        WorkshopToolCapability.PROPOSE_INVITE,
        WorkshopToolCapability.DRAFT_WORKFLOW,
        WorkshopToolCapability.CONFIRM_SAVE_WORKFLOW,
        WorkshopToolCapability.CREATE_SCHEDULE,
        WorkshopToolCapability.START_WORKFLOW_EXECUTION,
        WorkshopToolCapability.STOP_WORKFLOW_EXECUTION,
        WorkshopToolCapability.DELETE_WORKFLOW,
        WorkshopToolCapability.MANUAL_RUN_WORKFLOW,
    }
)

_ADVISOR: FrozenSet[WorkshopToolCapability] = frozenset(
    {
        WorkshopToolCapability.WEB_SEARCH,
        WorkshopToolCapability.WEB_FETCH_READONLY,
        WorkshopToolCapability.READ_UPLOADS,
        WorkshopToolCapability.READ_PROJECT_FILES,
        WorkshopToolCapability.BROWSER_READ,
        WorkshopToolCapability.PROPOSE_INVITE,
    }
)

_EXECUTOR: FrozenSet[WorkshopToolCapability] = frozenset(
    {
        WorkshopToolCapability.WEB_SEARCH,
        WorkshopToolCapability.WEB_FETCH_READONLY,
        WorkshopToolCapability.READ_UPLOADS,
        WorkshopToolCapability.READ_PROJECT_FILES,
        WorkshopToolCapability.WRITE_PROJECT_FILES,
        WorkshopToolCapability.WRITE_TEMP_WORKSPACE,
        WorkshopToolCapability.BROWSER_READ,
        WorkshopToolCapability.BROWSER_WRITE,
        WorkshopToolCapability.SANDBOX_EXECUTE,
        WorkshopToolCapability.MCP,
        WorkshopToolCapability.REQUEST_EXTERNAL_AUTH,
        WorkshopToolCapability.PROPOSE_INVITE,
        WorkshopToolCapability.TAOBAO_STORE_WRITE,
        WorkshopToolCapability.GENERATION_LIST_MODELS,
        WorkshopToolCapability.GENERATION_SUBMIT,
    }
)

_BY_ROLE: dict[WorkshopRole, FrozenSet[WorkshopToolCapability]] = {
    WorkshopRole.SINGLE_AGENT: _SINGLE_AGENT,
    WorkshopRole.HOST: _HOST,
    WorkshopRole.ADVISOR: _ADVISOR,
    WorkshopRole.EXECUTOR: _EXECUTOR,
}


def capabilities_for(role: WorkshopRole) -> FrozenSet[WorkshopToolCapability]:
    """返回角色允许的能力集合"""
    return _BY_ROLE[role]


def role_allows(role: WorkshopRole, capability: WorkshopToolCapability) -> bool:
    """判断角色是否允许使用某能力"""
    return capability in _BY_ROLE[role]
