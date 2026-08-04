from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import FrozenSet, Optional, Tuple, Union

from app.server.workshop.domain.enums import (
    WorkshopArtifactStorageType,
    WorkshopEventKind,
    WorkshopExpertKind,
    WorkshopProposalStatus,
    WorkshopRole,
    WorkshopTaskStatus,
    WorkshopToolCapability,
    WorkshopWorkflowRunStatus,
    WorkshopWorkflowRunTrigger,
    WorkshopWorkflowSource,
    WorkshopWorkflowStatus,
)
from app.server.workshop.domain.workflow_definition import WorkflowEdge, WorkflowNode


@dataclass(frozen=True, slots=True)
class ArtifactSubmission:
    """弱验收提交的产物；按存储类型显式校验"""

    name: str
    storage_type: WorkshopArtifactStorageType
    content: Optional[str] = None
    storage_key: Optional[str] = None
    size_bytes: Optional[int] = None

    def __post_init__(self) -> None:
        """按存储类型校验产物字段"""
        if not self.name.strip():
            raise ValueError("artifact name required")
        if self.storage_type is WorkshopArtifactStorageType.DB:
            if self.content is None or not self.content.strip():
                raise ValueError("db artifact content required")
            if self.storage_key is not None:
                raise ValueError("db artifact must not carry client storage_key")
            if self.size_bytes is not None and self.size_bytes <= 0:
                raise ValueError("db artifact size_bytes must be positive when set")
            return
        if self.storage_key is None or not self.storage_key.strip():
            raise ValueError("external artifact storage_key required")
        if self.size_bytes is None or self.size_bytes <= 0:
            raise ValueError("external artifact size_bytes required")
        if self.content is not None:
            raise ValueError("external artifact must not carry inline content")

    def is_non_empty(self) -> bool:
        """判断产物是否满足非空语义"""
        if self.storage_type is WorkshopArtifactStorageType.DB:
            return self.content is not None and bool(self.content.strip())
        return self.size_bytes is not None and self.size_bytes > 0


@dataclass(frozen=True, slots=True)
class CreateTaskProposal:
    """待用户确认的立任务提议"""

    id: str
    project_id: str
    title: str
    goals: Tuple[str, ...]
    required_artifacts: Tuple[str, ...]
    status: WorkshopProposalStatus


@dataclass(frozen=True, slots=True)
class RosterExpert:
    """项目名册上的专家"""

    id: str
    project_id: str
    name: str
    kind: WorkshopExpertKind
    preset_key: Optional[str] = None
    source_preset_key: Optional[str] = None


@dataclass(frozen=True, slots=True)
class WorkshopProjectRecord:
    """工坊项目读模型；主持角色为领域不变量"""

    id: str
    user_id: int
    name: str
    group_chat_id: int
    host_role: WorkshopRole = WorkshopRole.HOST


@dataclass(frozen=True, slots=True)
class WorkshopTaskRecord:
    """工坊任务读模型"""

    id: str
    project_id: str
    title: str
    goals: Tuple[str, ...]
    required_artifacts: Tuple[str, ...]
    status: WorkshopTaskStatus
    schedule_id: Optional[str]
    schedule_authorized: bool
    external_auth: FrozenSet[WorkshopToolCapability]
    revision: int


@dataclass(frozen=True, slots=True)
class WorkshopWorkflowRecord:
    """工坊工作流读模型（definition 为可执行 DAG）"""

    id: str
    project_id: str
    name: str
    nodes: Tuple[WorkflowNode, ...]
    edges: Tuple[WorkflowEdge, ...]
    status: WorkshopWorkflowStatus
    source: WorkshopWorkflowSource
    revision: int
    model_key: str
    entry_node_ids: Optional[Tuple[str, ...]] = None


@dataclass(frozen=True, slots=True)
class WorkshopWorkflowRunRecord:
    """工作流一次运行记录"""

    id: str
    project_id: str
    workflow_id: str
    workflow_revision: int
    schedule_id: Optional[str]
    trigger: WorkshopWorkflowRunTrigger
    status: WorkshopWorkflowRunStatus
    current_node_id: Optional[str]
    error_message: Optional[str]
    started_at: Optional[datetime]
    finished_at: Optional[datetime]
    created_at: datetime
    revision: int


@dataclass(frozen=True, slots=True)
class WorkshopScheduleRecord:
    """工坊定时读模型"""

    id: str
    project_id: str
    workflow_id: str
    cron: str
    timezone: str
    enabled: bool
    authorized_at: datetime
    authorized_external_capabilities: FrozenSet[WorkshopToolCapability]
    next_run_at: Optional[datetime]


@dataclass(frozen=True, slots=True)
class ScheduleStartedPayload:
    """定时开始事件载荷"""

    schedule_id: str
    task_id: str
    trigger_key: str
    message: str


@dataclass(frozen=True, slots=True)
class ScheduleSummaryPayload:
    """定时完成摘要事件载荷"""

    task_id: str
    message: str


@dataclass(frozen=True, slots=True)
class ArtifactsPublishedPayload:
    """产物发布事件载荷"""

    task_id: str
    artifact_names: Tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ScheduleBlockedPayload:
    """定时阻塞事件载荷"""

    task_id: str
    message: str
    reasons: Tuple[str, ...]


WorkshopEventPayload = Union[
    ScheduleStartedPayload,
    ScheduleSummaryPayload,
    ArtifactsPublishedPayload,
    ScheduleBlockedPayload,
]


@dataclass(frozen=True, slots=True)
class WorkshopEventRecord:
    """工坊事件读模型"""

    event_key: str
    project_id: str
    task_id: Optional[str]
    kind: WorkshopEventKind
    payload: WorkshopEventPayload
    created_at: datetime


@dataclass(frozen=True, slots=True)
class WorkshopScheduleRunRecord:
    """定时触发运行读模型"""

    id: str
    project_id: str
    schedule_id: str
    trigger_key: str
    task_id: str


@dataclass(frozen=True, slots=True)
class WeakAcceptResult:
    """弱验收结果"""

    passed: bool
    reasons: Tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ArtifactRecord:
    """工坊产物读模型"""

    id: str
    project_id: str
    task_id: Optional[str]
    name: str
    storage_type: WorkshopArtifactStorageType
    storage_key: str
    size_bytes: Optional[int]
    content: Optional[str]
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class TaskAssignmentRecord:
    """任务专家分配读模型"""

    task_id: str
    expert_ids: Tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ImportErrorRecord:
    """电商导入错误读模型"""

    row_index: int
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class MessageAttribution:
    """消息归属结果"""

    task_id: Optional[str]
    needs_clarification: bool
