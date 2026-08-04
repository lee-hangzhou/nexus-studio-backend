from __future__ import annotations

from app.server.workshop.domain.enums import WorkshopRole, WorkshopToolCapability
from app.server.workshop.domain.role_policy import role_allows


def test_single_agent_allows_light_tools_only() -> None:
    """单 Agent 只允许轻量工具"""
    assert role_allows(WorkshopRole.SINGLE_AGENT, WorkshopToolCapability.WEB_SEARCH)
    assert role_allows(
        WorkshopRole.SINGLE_AGENT, WorkshopToolCapability.WRITE_TEMP_WORKSPACE
    )
    assert not role_allows(WorkshopRole.SINGLE_AGENT, WorkshopToolCapability.BROWSER_WRITE)
    assert not role_allows(WorkshopRole.SINGLE_AGENT, WorkshopToolCapability.SANDBOX_EXECUTE)
    assert not role_allows(WorkshopRole.SINGLE_AGENT, WorkshopToolCapability.MCP)


def test_host_is_pure_scheduler() -> None:
    """主持只拥有调度与异步工作流编排能力"""
    assert role_allows(WorkshopRole.HOST, WorkshopToolCapability.INVITE_EXPERT)
    assert role_allows(WorkshopRole.HOST, WorkshopToolCapability.RAISE_AUTH_POPUP)
    assert role_allows(WorkshopRole.HOST, WorkshopToolCapability.DRAFT_WORKFLOW)
    assert role_allows(WorkshopRole.HOST, WorkshopToolCapability.CONFIRM_SAVE_WORKFLOW)
    assert role_allows(WorkshopRole.HOST, WorkshopToolCapability.CREATE_SCHEDULE)
    assert role_allows(WorkshopRole.HOST, WorkshopToolCapability.MANUAL_RUN_WORKFLOW)
    assert not role_allows(WorkshopRole.HOST, WorkshopToolCapability.WRITE_PROJECT_FILES)
    assert not role_allows(WorkshopRole.HOST, WorkshopToolCapability.SANDBOX_EXECUTE)
    assert not role_allows(WorkshopRole.HOST, WorkshopToolCapability.BROWSER_WRITE)
    assert not role_allows(WorkshopRole.HOST, WorkshopToolCapability.MCP)


def test_advisor_is_read_oriented() -> None:
    """顾问只拥有只读分析能力"""
    assert role_allows(WorkshopRole.ADVISOR, WorkshopToolCapability.READ_PROJECT_FILES)
    assert role_allows(WorkshopRole.ADVISOR, WorkshopToolCapability.WEB_SEARCH)
    assert role_allows(WorkshopRole.ADVISOR, WorkshopToolCapability.BROWSER_READ)
    assert role_allows(WorkshopRole.ADVISOR, WorkshopToolCapability.PROPOSE_INVITE)
    assert not role_allows(WorkshopRole.ADVISOR, WorkshopToolCapability.WRITE_PROJECT_FILES)
    assert not role_allows(WorkshopRole.ADVISOR, WorkshopToolCapability.BROWSER_WRITE)
    assert not role_allows(WorkshopRole.ADVISOR, WorkshopToolCapability.MCP)
    assert not role_allows(WorkshopRole.ADVISOR, WorkshopToolCapability.SANDBOX_EXECUTE)
    assert not role_allows(WorkshopRole.ADVISOR, WorkshopToolCapability.DRAFT_WORKFLOW)


def test_executor_can_write_but_not_host_workflow_or_raise_auth_popup() -> None:
    """执行型可写，但不能 Host 工作流编排与直接弹授权"""
    assert role_allows(WorkshopRole.EXECUTOR, WorkshopToolCapability.WRITE_PROJECT_FILES)
    assert role_allows(WorkshopRole.EXECUTOR, WorkshopToolCapability.SANDBOX_EXECUTE)
    assert role_allows(WorkshopRole.EXECUTOR, WorkshopToolCapability.BROWSER_WRITE)
    assert role_allows(WorkshopRole.EXECUTOR, WorkshopToolCapability.MCP)
    assert role_allows(WorkshopRole.EXECUTOR, WorkshopToolCapability.REQUEST_EXTERNAL_AUTH)
    assert role_allows(WorkshopRole.EXECUTOR, WorkshopToolCapability.PROPOSE_INVITE)
    assert role_allows(WorkshopRole.EXECUTOR, WorkshopToolCapability.TAOBAO_STORE_WRITE)
    assert role_allows(WorkshopRole.EXECUTOR, WorkshopToolCapability.GENERATION_LIST_MODELS)
    assert role_allows(WorkshopRole.EXECUTOR, WorkshopToolCapability.GENERATION_SUBMIT)
    assert not role_allows(WorkshopRole.EXECUTOR, WorkshopToolCapability.DRAFT_WORKFLOW)
    assert not role_allows(WorkshopRole.EXECUTOR, WorkshopToolCapability.CREATE_SCHEDULE)
    assert not role_allows(WorkshopRole.EXECUTOR, WorkshopToolCapability.RAISE_AUTH_POPUP)
    assert not role_allows(WorkshopRole.EXECUTOR, WorkshopToolCapability.INVITE_EXPERT)
