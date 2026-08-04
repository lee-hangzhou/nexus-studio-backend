from __future__ import annotations

from enum import Enum


class WorkshopTaskStatus(str, Enum):
    """工坊任务状态"""

    ALIGNING = "aligning"
    AWAITING_GO = "awaiting_go"
    AUTHORIZED = "authorized"
    EXECUTING = "executing"
    BLOCKED = "blocked"
    RE_ALIGNING = "re_aligning"
    REVIEWING = "reviewing"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


class WorkshopRole(str, Enum):
    """工坊参与角色"""

    SINGLE_AGENT = "single_agent"
    HOST = "host"
    ADVISOR = "advisor"
    EXECUTOR = "executor"


class WorkshopExpertKind(str, Enum):
    """专家岗位类型"""

    ADVISOR = "advisor"
    EXECUTOR = "executor"


class WorkshopChatMode(str, Enum):
    """超级工坊入口形态"""

    SINGLE_AGENT = "single_agent"
    WORKSHOP = "workshop"


class WorkshopWorkflowStatus(str, Enum):
    """工坊工作流定义状态"""

    DRAFT = "draft"
    SAVED = "saved"


class WorkshopWorkflowSource(str, Enum):
    """工作流草稿来源"""

    AGENT = "agent"
    USER = "user"


class WorkshopWorkflowRunStatus(str, Enum):
    """工作流一次运行的状态"""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BLOCKED = "blocked"


class WorkshopWorkflowRunTrigger(str, Enum):
    """工作流运行触发方式"""

    MANUAL = "manual"
    SCHEDULE = "schedule"
    TRIAL = "trial"


class WorkshopProposalStatus(str, Enum):
    """立任务/定制专家提议状态"""

    PENDING = "pending"
    CONFIRMED = "confirmed"
    DECLINED = "declined"


class WorkshopArtifactStorageType(str, Enum):
    """工坊产物存储类型"""

    DB = "db"
    OSS = "oss"
    FILESYSTEM = "filesystem"


class WorkshopEventKind(str, Enum):
    """工坊可观察事件种类"""

    SCHEDULE_STARTED = "schedule_started"
    SCHEDULE_SUMMARY = "schedule_summary"
    ARTIFACTS_PUBLISHED = "artifacts_published"
    SCHEDULE_BLOCKED = "schedule_blocked"


class WorkshopToolCapability(str, Enum):
    """角色工具能力枚举（硬白名单用）"""

    WEB_SEARCH = "web_search"
    WEB_FETCH_READONLY = "web_fetch_readonly"
    READ_UPLOADS = "read_uploads"
    WRITE_TEMP_WORKSPACE = "write_temp_workspace"
    READ_PROJECT_FILES = "read_project_files"
    WRITE_PROJECT_FILES = "write_project_files"
    BROWSER_READ = "browser_read"
    BROWSER_WRITE = "browser_write"
    SANDBOX_EXECUTE = "sandbox_execute"
    MCP = "mcp"
    DRAFT_WORKFLOW = "draft_workflow"
    CONFIRM_SAVE_WORKFLOW = "confirm_save_workflow"
    CREATE_SCHEDULE = "create_schedule"
    MANUAL_RUN_WORKFLOW = "manual_run_workflow"
    REQUEST_EXTERNAL_AUTH = "request_external_auth"
    PROPOSE_INVITE = "propose_invite"
    INVITE_EXPERT = "invite_expert"
    RAISE_AUTH_POPUP = "raise_auth_popup"
    # Ecommerce connector / write capabilities
    TAOBAO_STORE_WRITE = "taobao_store_write"
    GENERATION_LIST_MODELS = "generation_list_models"
    GENERATION_SUBMIT = "generation_submit"
