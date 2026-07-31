from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.contracts.ecommerce import ShopConnectionPublic
from app.server.workshop.domain.enums import (
    WorkshopArtifactStorageType,
    WorkshopEventKind,
    WorkshopExpertKind,
    WorkshopProposalStatus,
    WorkshopRole,
    WorkshopTaskStatus,
    WorkshopToolCapability,
    WorkshopWorkflowSource,
    WorkshopWorkflowStatus,
)


class WorkshopContract(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)


class WorkshopProjectView(WorkshopContract):
    id: str
    user_id: int
    name: str
    group_chat_id: int
    host_role: WorkshopRole


class WorkshopRosterExpertView(WorkshopContract):
    id: str
    project_id: str
    name: str
    kind: WorkshopExpertKind
    preset_key: str | None = None
    source_preset_key: str | None = None
    display_name: str | None = None
    role_phrase: str | None = None
    avatar_id: str | None = None
    capability_allowlist: list[WorkshopToolCapability] = Field(default_factory=list)
    skill_refs: list[str] = Field(default_factory=list)
    deliverable_types: list[str] = Field(default_factory=list)
    beta: bool = False


class WorkshopCreateTaskProposalView(WorkshopContract):
    id: str
    project_id: str
    title: str
    goals: list[str]
    required_artifacts: list[str]
    status: WorkshopProposalStatus


class WorkshopTaskView(WorkshopContract):
    id: str
    project_id: str
    title: str
    goals: list[str]
    required_artifacts: list[str]
    status: WorkshopTaskStatus
    schedule_id: str | None
    schedule_authorized: bool
    external_auth: list[WorkshopToolCapability]
    revision: int


class WorkshopWorkflowStepView(WorkshopContract):
    title: str = Field(min_length=1)
    required_artifact_names: list[str] = Field(default_factory=list)
    external_capabilities: list[WorkshopToolCapability] = Field(default_factory=list)


class WorkshopWorkflowView(WorkshopContract):
    id: str
    project_id: str
    name: str
    steps: list[WorkshopWorkflowStepView]
    status: WorkshopWorkflowStatus
    source: WorkshopWorkflowSource
    revision: int


class WorkshopScheduleView(WorkshopContract):
    id: str
    project_id: str
    workflow_id: str
    cron: str
    timezone: str
    enabled: bool
    authorized_at: str
    authorized_external_capabilities: list[WorkshopToolCapability]
    next_run_at: str | None


class WorkshopScheduleRunView(WorkshopContract):
    id: str
    project_id: str
    schedule_id: str
    trigger_key: str
    task_id: str


class ArtifactSubmissionView(WorkshopContract):
    name: str = Field(min_length=1)
    storage_type: WorkshopArtifactStorageType
    content: str | None = None
    storage_key: str | None = None
    size_bytes: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_storage_fields(self) -> ArtifactSubmissionView:
        """按存储类型校验产物字段"""
        storage = self.storage_type
        if isinstance(storage, str):
            storage = WorkshopArtifactStorageType(storage)
        if storage is WorkshopArtifactStorageType.DB:
            if self.content is None or not self.content.strip():
                raise ValueError("db artifact content required")
            if self.storage_key is not None:
                raise ValueError("db artifact must not carry client storage_key")
            return self
        if self.storage_key is None or not self.storage_key.strip():
            raise ValueError("external artifact storage_key required")
        if self.size_bytes is None:
            raise ValueError("external artifact size_bytes required")
        if self.content is not None:
            raise ValueError("external artifact must not carry inline content")
        return self


class ScheduleStartedPayloadView(WorkshopContract):
    schedule_id: str
    task_id: str
    trigger_key: str
    message: str


class ScheduleSummaryPayloadView(WorkshopContract):
    task_id: str
    message: str


class ArtifactsPublishedPayloadView(WorkshopContract):
    task_id: str
    artifact_names: list[str]


class ScheduleBlockedPayloadView(WorkshopContract):
    task_id: str
    message: str
    reasons: list[str]


WorkshopEventPayloadView = Annotated[
    ScheduleStartedPayloadView
    | ScheduleSummaryPayloadView
    | ArtifactsPublishedPayloadView
    | ScheduleBlockedPayloadView,
    Field(union_mode="left_to_right"),
]


class WorkshopEventView(WorkshopContract):
    event_key: str
    project_id: str
    task_id: str | None
    kind: WorkshopEventKind
    payload: WorkshopEventPayloadView
    created_at: str

    @model_validator(mode="after")
    def validate_payload_matches_kind(self) -> WorkshopEventView:
        """事件 kind 与载荷类型必须一致"""
        kind = self.kind if isinstance(self.kind, WorkshopEventKind) else WorkshopEventKind(self.kind)
        expected: dict[WorkshopEventKind, type] = {
            WorkshopEventKind.SCHEDULE_STARTED: ScheduleStartedPayloadView,
            WorkshopEventKind.SCHEDULE_SUMMARY: ScheduleSummaryPayloadView,
            WorkshopEventKind.ARTIFACTS_PUBLISHED: ArtifactsPublishedPayloadView,
            WorkshopEventKind.SCHEDULE_BLOCKED: ScheduleBlockedPayloadView,
        }
        if not isinstance(self.payload, expected[kind]):
            raise ValueError(f"payload type mismatch for event kind {kind.value}")
        return self


class WorkshopWeakAcceptResultView(WorkshopContract):
    passed: bool
    reasons: list[str] = Field(default_factory=list)


class WorkshopOkView(WorkshopContract):
    ok: Literal[True] = True


class WorkshopWakeDashboardView(WorkshopContract):
    group_chat_id: int
    pinned_task_ids: list[str]


class WorkshopUpgradeResultView(WorkshopContract):
    project: WorkshopProjectView
    group_chat_id: int
    carried_message_count: int
    pending_task_proposal: WorkshopCreateTaskProposalView | None = None


class WorkshopManualRunResultView(WorkshopContract):
    task: WorkshopTaskView
    used_light_confirmation: bool


class WorkshopScheduleTriggerResultView(WorkshopContract):
    task: WorkshopTaskView
    run: WorkshopScheduleRunView
    event_ids: list[str]
    requires_external_auth_popup: bool


class WorkshopScheduledCompletionResultView(WorkshopContract):
    task_status: WorkshopTaskStatus
    event_ids: list[str]


class WorkshopRosterListResponse(WorkshopContract):
    items: list[WorkshopRosterExpertView]


class WorkshopRoomMembersResponse(WorkshopContract):
    expert_ids: list[str]


class WorkshopWorkflowListResponse(WorkshopContract):
    items: list[WorkshopWorkflowView]


class WorkshopEventListResponse(WorkshopContract):
    items: list[WorkshopEventView]


class WorkshopExpertProposalIdView(WorkshopContract):
    proposal_id: str


class WorkshopCreateProjectRequest(WorkshopContract):
    name: str = Field(min_length=1, max_length=255)
    group_chat_id: int = Field(ge=1)
    initial_expert_keys: list[str] = Field(default_factory=list)


class WorkshopProjectIdRequest(WorkshopContract):
    project_id: str = Field(min_length=1)


class WorkshopGroupChatIdRequest(WorkshopContract):
    group_chat_id: int = Field(ge=1)


class WorkshopProjectByChatResponse(WorkshopContract):
    project: WorkshopProjectView | None = None


class WorkshopUpgradeRequest(WorkshopContract):
    group_chat_id: int = Field(ge=1)
    project_name: str = Field(min_length=1, max_length=255)
    # 兼容旧客户端；升级不再据此自动创建任务提议
    seed_goal: str = ""
    carried_message_count: int = Field(ge=0)
    initial_expert_keys: list[str] = Field(default_factory=list)


class WorkshopCopyPresetRequest(WorkshopContract):
    project_id: str = Field(min_length=1)
    preset_key: str = Field(min_length=1)
    name: str = Field(min_length=1, max_length=255)


class WorkshopAddPresetRequest(WorkshopContract):
    """将平台预置专家加入项目名册（保留 preset_key，供运行时挂载）"""

    project_id: str = Field(min_length=1)
    preset_key: str = Field(min_length=1)


class WorkshopProposeCustomExpertRequest(WorkshopContract):
    project_id: str = Field(min_length=1)
    name: str = Field(min_length=1, max_length=255)
    kind: WorkshopExpertKind


class WorkshopConfirmCustomExpertRequest(WorkshopContract):
    project_id: str = Field(min_length=1)
    proposal_id: str = Field(min_length=1)


class WorkshopInviteExpertRequest(WorkshopContract):
    project_id: str = Field(min_length=1)
    expert_id: str = Field(min_length=1)


class WorkshopRemoveExpertRequest(WorkshopContract):
    project_id: str = Field(min_length=1)
    expert_id: str = Field(min_length=1)


class WorkshopAssignTaskExpertRequest(WorkshopContract):
    project_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    expert_id: str = Field(min_length=1)


class WorkshopUnassignTaskExpertRequest(WorkshopContract):
    project_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    expert_id: str = Field(min_length=1)


class WorkshopProposeTaskRequest(WorkshopContract):
    project_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    goals: list[str] = Field(min_length=1)
    required_artifacts: list[str] = Field(default_factory=list)


class WorkshopConfirmTaskProposalRequest(WorkshopContract):
    project_id: str = Field(min_length=1)
    proposal_id: str = Field(min_length=1)


class WorkshopDeclineTaskProposalRequest(WorkshopContract):
    project_id: str = Field(min_length=1)
    proposal_id: str = Field(min_length=1)


class WorkshopTaskIdRequest(WorkshopContract):
    project_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)


class WorkshopGrantExternalAuthRequest(WorkshopContract):
    project_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    capabilities: list[WorkshopToolCapability] = Field(min_length=1)


class WorkshopRecordCapabilityUseRequest(WorkshopContract):
    project_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    capability: WorkshopToolCapability


class WorkshopWeakAcceptRequest(WorkshopContract):
    project_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    covered_goals: list[str] = Field(default_factory=list)
    artifacts: list[ArtifactSubmissionView] = Field(default_factory=list)


class WorkshopDraftWorkflowRequest(WorkshopContract):
    project_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    steps: list[WorkshopWorkflowStepView] = Field(min_length=1)


class WorkshopConfirmWorkflowRequest(WorkshopContract):
    project_id: str = Field(min_length=1)
    workflow_id: str = Field(min_length=1)


class WorkshopManualRunWorkflowRequest(WorkshopContract):
    project_id: str = Field(min_length=1)
    workflow_id: str = Field(min_length=1)
    authorized_capabilities: list[WorkshopToolCapability] = Field(default_factory=list)


class WorkshopCreateScheduleRequest(WorkshopContract):
    project_id: str = Field(min_length=1)
    workflow_id: str = Field(min_length=1)
    cron: str = Field(min_length=1)
    timezone: str = Field(min_length=1)
    authorized_capabilities: list[WorkshopToolCapability] = Field(default_factory=list)


class WorkshopScheduleIdRequest(WorkshopContract):
    project_id: str = Field(min_length=1)
    schedule_id: str = Field(min_length=1)


class WorkshopScheduleListResponse(WorkshopContract):
    items: list[WorkshopScheduleView]


class WorkshopTriggerScheduleRequest(WorkshopContract):
    project_id: str = Field(min_length=1)
    schedule_id: str = Field(min_length=1)
    trigger_key: str = Field(min_length=1)


class WorkshopCompleteScheduledRunRequest(WorkshopContract):
    project_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    covered_goals: list[str] = Field(default_factory=list)
    artifacts: list[ArtifactSubmissionView] = Field(default_factory=list)


class WorkshopDeclineCustomExpertRequest(WorkshopContract):
    project_id: str = Field(min_length=1)
    proposal_id: str = Field(min_length=1)


class WorkshopAuthorizeOperationRequest(WorkshopContract):
    project_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    capability: WorkshopToolCapability
    operation_kind: str = Field(min_length=1)
    payload_hash: str = Field(min_length=1)


class WorkshopAuthorizedOperationView(WorkshopContract):
    id: str
    task_id: str
    capability: WorkshopToolCapability
    operation_kind: str
    payload_hash: str
    granted_by: int
    granted_at: str
    consumed_at: str | None = None
    revoked_at: str | None = None


class WorkshopInviteDirectoryExpertView(WorkshopContract):
    key: str
    name: str
    kind: WorkshopExpertKind
    beta: bool = False
    deliverable_types: list[str] = Field(default_factory=list)


class WorkshopInviteDirectoryResponse(WorkshopContract):
    items: list[WorkshopInviteDirectoryExpertView]


class WorkshopProjectListResponse(WorkshopContract):
    items: list[WorkshopProjectView]


class WorkshopTaskListResponse(WorkshopContract):
    items: list[WorkshopTaskView]


class WorkshopArtifactView(WorkshopContract):
    id: str
    project_id: str
    task_id: str | None
    name: str
    storage_type: WorkshopArtifactStorageType
    storage_key: str
    size_bytes: int | None = None
    content: str | None = None
    created_at: str
    updated_at: str


class WorkshopArtifactListRequest(WorkshopContract):
    project_id: str = Field(min_length=1)
    task_id: str | None = None


class WorkshopArtifactListResponse(WorkshopContract):
    items: list[WorkshopArtifactView]


class WorkshopAuthorizedOperationListResponse(WorkshopContract):
    items: list[WorkshopAuthorizedOperationView]


class WorkshopPendingProposalListResponse(WorkshopContract):
    items: list[WorkshopCreateTaskProposalView]


class WorkshopTaskAssignmentView(WorkshopContract):
    task_id: str
    expert_ids: list[str]


class WorkshopTaskAssignmentListResponse(WorkshopContract):
    items: list[WorkshopTaskAssignmentView]


class WorkshopDataSourceStatus(WorkshopContract):
    key: str
    label: str
    status: Literal[
        "disconnected",
        "authorizing",
        "connected",
        "reauth_required",
        "permission_denied",
        "syncing",
        "sync_failed",
        "incomplete",
    ]
    detail: str | None = None
    last_synced_at: str | None = None


class WorkshopDataSourcesView(WorkshopContract):
    shop: ShopConnectionPublic | None = None
    sources: list[WorkshopDataSourceStatus] = Field(default_factory=list)
    quality_issues: list[str] = Field(default_factory=list)


class WorkshopImportErrorView(WorkshopContract):
    row_index: int
    code: str
    message: str


class WorkshopImportErrorListResponse(WorkshopContract):
    items: list[WorkshopImportErrorView]
    unsupported_template: bool = False


class WorkshopBeginShopAuthView(WorkshopContract):
    authorize_url: str | None = None
    shop_connection_id: str
    status: Literal["authorizing"] = "authorizing"


class ExpertDirectoryEntry(WorkshopContract):
    key: str
    name: str
    role_phrase: str
    tags: list[str] = Field(min_length=2, max_length=3)
    scenes: list[str] = Field(min_length=1)
    avatar_id: str
    kind: Literal["expert"] = "expert"
    applicable_tasks: list[str] = Field(default_factory=list)


class ExpertTeamDirectoryEntry(WorkshopContract):
    key: str
    name: str
    role_phrase: str
    tags: list[str] = Field(min_length=2, max_length=3)
    scenes: list[str] = Field(min_length=1)
    avatar_id: str
    kind: Literal["team"] = "team"
    member_keys: list[str] = Field(min_length=1)
    applicable_tasks: list[str] = Field(default_factory=list)


class ExpertDirectoryResponse(WorkshopContract):
    items: list[ExpertDirectoryEntry]


class ExpertTeamDirectoryResponse(WorkshopContract):
    items: list[ExpertTeamDirectoryEntry]


class ChatSelectedExpertView(WorkshopContract):
    conversation_id: int
    expert_key: str | None = None
    name: str | None = None
    avatar_id: str | None = None


class SetChatSelectedExpertRequest(WorkshopContract):
    conversation_id: int = Field(ge=1)
    expert_key: str = Field(min_length=1)


class ClearChatSelectedExpertRequest(WorkshopContract):
    conversation_id: int = Field(ge=1)


class GetChatSelectedExpertRequest(WorkshopContract):
    conversation_id: int = Field(ge=1)


class WorkshopUpgradeFromTeamRequest(WorkshopContract):
    conversation_id: int = Field(ge=1)
    team_key: str = Field(min_length=1)


class WorkshopUpgradeFromExpertRequest(WorkshopContract):
    """从单专家邀请原地升级为项目"""

    conversation_id: int = Field(ge=1)
    expert_key: str = Field(min_length=1)
    project_name: str = Field(min_length=1, max_length=255)
    # 兼容旧客户端；升级不再据此自动创建任务提议
    seed_goal: str = ""
    carried_message_count: int = Field(ge=0)


class WorkshopTeamSelectRequiresUpgradeView(WorkshopContract):
    requires_upgrade: Literal[True] = True
    team_key: str
    message: str




class WorkshopConnectorListRequest(WorkshopContract):
    project_id: str | None = None


class WorkshopConnectorEntry(WorkshopContract):
    """可验证连接器目录项（状态来自真实 data_sources，非静态冒充）"""

    key: str
    name: str
    description: str
    status: Literal[
        "disconnected",
        "authorizing",
        "connected",
        "reauth_required",
        "permission_denied",
        "syncing",
        "sync_failed",
        "incomplete",
        "unsupported",
    ]
    detail: str | None = None


class WorkshopConnectorDirectoryResponse(WorkshopContract):
    items: list[WorkshopConnectorEntry]

class WorkshopTurnTarget(WorkshopContract):
    expert_id: str | None = None
    task_id: str | None = None
    speaker_role: str | None = None
