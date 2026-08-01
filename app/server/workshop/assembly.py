from __future__ import annotations

from datetime import datetime

from app.contracts.workshop import (
    ArtifactsPublishedPayloadView,
    ArtifactSubmissionView,
    ScheduleBlockedPayloadView,
    ScheduleStartedPayloadView,
    ScheduleSummaryPayloadView,
    WorkshopArtifactView,
    WorkshopAuthorizedOperationView,
    WorkshopBeginShopAuthView,
    WorkshopCreateTaskProposalView,
    WorkshopDataSourcesView,
    WorkshopDataSourceStatus,
    WorkshopEventView,
    ExpertDirectoryEntry,
    ExpertDirectoryResponse,
    ExpertTeamDirectoryResponse,
    WorkshopImportErrorListResponse,
    WorkshopImportErrorView,
    WorkshopInviteDirectoryExpertView,
    WorkshopInviteDirectoryResponse,
    WorkshopManualRunResultView,
    WorkshopProjectView,
    WorkshopRosterExpertView,
    WorkshopScheduledCompletionResultView,
    WorkshopScheduleRunView,
    WorkshopScheduleTriggerResultView,
    WorkshopScheduleView,
    WorkshopTaskAssignmentView,
    WorkshopTaskView,
    WorkshopUpgradeResultView,
    WorkshopUpgradeInviteExpertView,
    WorkshopUpgradeInviteProposedView,
    WorkshopWakeDashboardView,
    WorkshopWeakAcceptResultView,
    WorkshopWorkflowStepView,
    WorkshopWorkflowView,
)
from app.server.workshop.domain.ecommerce.authorized_operations import AuthorizedOperation
from app.server.chat.services.upgrade_invite import UpgradeInviteProposalRecord
from app.server.workshop.domain.ecommerce.profiles import get_ecom_profile, is_ecom_preset
from app.server.workshop.domain.enums import (
    WorkshopArtifactStorageType,
    WorkshopRole,
    WorkshopTaskStatus,
    WorkshopToolCapability,
)
from app.server.workshop.domain.presets import list_invite_directory
from app.server.workshop.domain.expert_catalog import (
    list_expert_directory,
    get_catalog_entry,
)
from app.server.workshop.domain.types import (
    ArtifactsPublishedPayload,
    ArtifactRecord,
    ArtifactSubmission,
    CreateTaskProposal,
    RosterExpert,
    ScheduleBlockedPayload,
    ScheduleStartedPayload,
    ScheduleSummaryPayload,
    TaskAssignmentRecord,
    WeakAcceptResult,
    WorkflowStep,
    WorkshopEventPayload,
    WorkshopEventRecord,
    WorkshopProjectRecord,
    WorkshopScheduleRecord,
    WorkshopScheduleRunRecord,
    WorkshopTaskRecord,
    WorkshopWorkflowRecord,
)
from app.server.workshop.services.workflow_schedule_service import (
    ManualRunResult,
    ScheduledCompletionResult,
    ScheduleTriggerResult,
)
from app.server.workshop.services.workshop_project_service import (
    BeginShopAuthRecord,
    DataSourcesRecord,
    ImportErrorsRecord,
    UpgradeResult,
    WakeDashboard,
    WorkshopProject,
)


def _iso(value: datetime) -> str:
    """将 datetime 格式化为 ISO8601"""
    return value.isoformat()


def project_to_view(project: WorkshopProject | WorkshopProjectRecord) -> WorkshopProjectView:
    """工坊项目读模型转契约视图；host_role 为领域不变量 HOST"""
    if project.host_role is not WorkshopRole.HOST:
        raise ValueError("workshop project host_role must be HOST")
    return WorkshopProjectView(
        id=project.id,
        user_id=project.user_id,
        name=project.name,
        group_chat_id=project.group_chat_id,
        host_role=WorkshopRole.HOST,
    )


def _roster_profile_fields(preset_key: str | None) -> dict[str, object]:
    """电商 preset 填充 profile 字段；通用 preset 返回空/默认"""
    if preset_key is None or not is_ecom_preset(preset_key):
        return {
            "capability_allowlist": [],
            "skill_refs": [],
            "deliverable_types": [],
            "beta": False,
        }
    profile = get_ecom_profile(preset_key)
    return {
        "capability_allowlist": sorted(profile.capability_allowlist, key=lambda c: c.value),
        "skill_refs": list(profile.skill_refs),
        "deliverable_types": list(profile.deliverable_types),
        "beta": profile.beta,
    }


def roster_expert_to_view(expert: RosterExpert) -> WorkshopRosterExpertView:
    """名册专家转契约视图"""
    profile_fields = _roster_profile_fields(expert.preset_key)
    catalog_fields: dict[str, object] = {}
    if expert.preset_key:
        try:
            catalog = get_catalog_entry(expert.preset_key)
            catalog_fields = {
                "display_name": catalog.name,
                "role_phrase": catalog.role_phrase,
                "avatar_id": catalog.avatar_id,
            }
        except KeyError:
            catalog_fields = {
                "display_name": expert.name,
                "role_phrase": None,
                "avatar_id": None,
            }
    else:
        catalog_fields = {
            "display_name": expert.name,
            "role_phrase": None,
            "avatar_id": None,
        }
    return WorkshopRosterExpertView(
        id=expert.id,
        project_id=expert.project_id,
        name=expert.name,
        kind=expert.kind,
        preset_key=expert.preset_key,
        source_preset_key=expert.source_preset_key,
        **catalog_fields,
        **profile_fields,
    )


def authorized_operation_to_view(
    operation: AuthorizedOperation,
) -> WorkshopAuthorizedOperationView:
    """AuthorizedOperation 转契约视图"""
    return WorkshopAuthorizedOperationView(
        id=operation.id,
        task_id=operation.task_id,
        capability=operation.capability,
        operation_kind=operation.operation_kind,
        payload_hash=operation.payload_hash,
        granted_by=operation.granted_by,
        granted_at=_iso(operation.granted_at),
        consumed_at=_iso(operation.consumed_at) if operation.consumed_at else None,
        revoked_at=_iso(operation.revoked_at) if operation.revoked_at else None,
    )


def invite_directory_to_view() -> WorkshopInviteDirectoryResponse:
    """可邀请专家目录：仅产品业务专家"""
    items: list[WorkshopInviteDirectoryExpertView] = []
    for preset in list_invite_directory():
        profile = get_ecom_profile(preset.key)
        items.append(
            WorkshopInviteDirectoryExpertView(
                key=preset.key,
                name=preset.name,
                kind=preset.kind,
                beta=profile.beta,
                deliverable_types=list(profile.deliverable_types),
            )
        )
    return WorkshopInviteDirectoryResponse(items=items)


def expert_directory_to_view() -> ExpertDirectoryResponse:
    """用户可见专家目录契约视图"""
    items = [
        ExpertDirectoryEntry(
            key=entry.key,
            name=entry.name,
            role_phrase=entry.role_phrase,
            tags=list(entry.tags),
            scenes=list(entry.scenes),
            avatar_id=entry.avatar_id,
            kind=entry.kind,
            applicable_tasks=list(entry.applicable_tasks),
        )
        for entry in list_expert_directory()
    ]
    return ExpertDirectoryResponse(items=items)


def expert_teams_to_view() -> ExpertTeamDirectoryResponse:
    """用户可见专家团队目录契约视图（已停用，恒为空）"""
    return ExpertTeamDirectoryResponse(items=[])


def proposal_to_view(proposal: CreateTaskProposal) -> WorkshopCreateTaskProposalView:
    """立任务提议转契约视图"""
    return WorkshopCreateTaskProposalView(
        id=proposal.id,
        project_id=proposal.project_id,
        title=proposal.title,
        goals=list(proposal.goals),
        required_artifacts=list(proposal.required_artifacts),
        status=proposal.status,
    )


def task_to_view(task: WorkshopTaskRecord) -> WorkshopTaskView:
    """任务读模型转契约视图"""
    return WorkshopTaskView(
        id=task.id,
        project_id=task.project_id,
        title=task.title,
        goals=list(task.goals),
        required_artifacts=list(task.required_artifacts),
        status=task.status,
        schedule_id=task.schedule_id,
        schedule_authorized=task.schedule_authorized,
        external_auth=sorted(task.external_auth, key=lambda item: item.value),
        revision=task.revision,
    )


def artifact_to_view(record: ArtifactRecord) -> WorkshopArtifactView:
    """产物读模型转契约视图"""
    return WorkshopArtifactView(
        id=record.id,
        project_id=record.project_id,
        task_id=record.task_id,
        name=record.name,
        storage_type=record.storage_type,
        storage_key=record.storage_key,
        size_bytes=record.size_bytes,
        content=record.content,
        created_at=_iso(record.created_at),
        updated_at=_iso(record.updated_at),
    )


def task_assignment_to_view(record: TaskAssignmentRecord) -> WorkshopTaskAssignmentView:
    """任务专家分配转契约视图"""
    return WorkshopTaskAssignmentView(
        task_id=record.task_id,
        expert_ids=sorted(record.expert_ids),
    )


def data_sources_to_view(record: DataSourcesRecord) -> WorkshopDataSourcesView:
    """电商数据源读模型转契约视图"""
    return WorkshopDataSourcesView(
        shop=record.shop,
        sources=[
            WorkshopDataSourceStatus(
                key=source.key,
                label=source.label,
                status=source.status,
                detail=source.detail,
                last_synced_at=source.last_synced_at,
            )
            for source in record.sources
        ],
        quality_issues=list(record.quality_issues),
    )


def import_errors_to_view(record: ImportErrorsRecord) -> WorkshopImportErrorListResponse:
    """导入错误读模型转契约视图"""
    return WorkshopImportErrorListResponse(
        items=[
            WorkshopImportErrorView(
                row_index=item.row_index,
                code=item.code,
                message=item.message,
            )
            for item in record.items
        ],
        unsupported_template=record.unsupported_template,
    )


def begin_shop_auth_to_view(record: BeginShopAuthRecord) -> WorkshopBeginShopAuthView:
    """发起店铺授权读模型转契约视图"""
    return WorkshopBeginShopAuthView(
        authorize_url=record.authorize_url,
        shop_connection_id=record.shop_connection_id,
        status="authorizing",
    )


def workflow_step_to_view(step: WorkflowStep) -> WorkshopWorkflowStepView:
    """工作流步骤转契约视图"""
    return WorkshopWorkflowStepView(
        title=step.title,
        required_artifact_names=list(step.required_artifact_names),
        external_capabilities=list(step.external_capabilities),
    )


def workflow_to_view(workflow: WorkshopWorkflowRecord) -> WorkshopWorkflowView:
    """工作流转契约视图"""
    return WorkshopWorkflowView(
        id=workflow.id,
        project_id=workflow.project_id,
        name=workflow.name,
        steps=[workflow_step_to_view(step) for step in workflow.steps],
        status=workflow.status,
        source=workflow.source,
        revision=workflow.revision,
    )


def schedule_to_view(schedule: WorkshopScheduleRecord) -> WorkshopScheduleView:
    """定时读模型转契约视图"""
    return WorkshopScheduleView(
        id=schedule.id,
        project_id=schedule.project_id,
        workflow_id=schedule.workflow_id,
        cron=schedule.cron,
        timezone=schedule.timezone,
        enabled=schedule.enabled,
        authorized_at=_iso(schedule.authorized_at),
        authorized_external_capabilities=sorted(
            schedule.authorized_external_capabilities, key=lambda item: item.value
        ),
        next_run_at=_iso(schedule.next_run_at) if schedule.next_run_at is not None else None,
    )


def schedule_run_to_view(run: WorkshopScheduleRunRecord) -> WorkshopScheduleRunView:
    """定时运行读模型转契约视图"""
    return WorkshopScheduleRunView(
        id=run.id,
        project_id=run.project_id,
        schedule_id=run.schedule_id,
        trigger_key=run.trigger_key,
        task_id=run.task_id,
    )


def _event_payload_to_view(payload: WorkshopEventPayload):
    """事件载荷转契约视图"""
    if isinstance(payload, ScheduleStartedPayload):
        return ScheduleStartedPayloadView(
            schedule_id=payload.schedule_id,
            task_id=payload.task_id,
            trigger_key=payload.trigger_key,
            message=payload.message,
        )
    if isinstance(payload, ScheduleSummaryPayload):
        return ScheduleSummaryPayloadView(task_id=payload.task_id, message=payload.message)
    if isinstance(payload, ArtifactsPublishedPayload):
        return ArtifactsPublishedPayloadView(
            task_id=payload.task_id,
            artifact_names=list(payload.artifact_names),
        )
    if isinstance(payload, ScheduleBlockedPayload):
        return ScheduleBlockedPayloadView(
            task_id=payload.task_id,
            message=payload.message,
            reasons=list(payload.reasons),
        )
    raise TypeError(f"unsupported workshop event payload: {type(payload)!r}")


def event_to_view(event: WorkshopEventRecord) -> WorkshopEventView:
    """工坊事件转契约视图"""
    return WorkshopEventView(
        event_key=event.event_key,
        project_id=event.project_id,
        task_id=event.task_id,
        kind=event.kind,
        payload=_event_payload_to_view(event.payload),
        created_at=_iso(event.created_at),
    )


def weak_accept_to_view(result: WeakAcceptResult) -> WorkshopWeakAcceptResultView:
    """弱验收结果转契约视图"""
    return WorkshopWeakAcceptResultView(passed=result.passed, reasons=list(result.reasons))


def upgrade_to_view(result: UpgradeResult) -> WorkshopUpgradeResultView:
    """升级结果转契约视图"""
    return WorkshopUpgradeResultView(
        project=project_to_view(result.project),
        group_chat_id=result.group_chat_id,
        carried_message_count=result.carried_message_count,
        pending_task_proposal=(
            proposal_to_view(result.pending_task_proposal)
            if result.pending_task_proposal is not None
            else None
        ),
    )


def wake_to_view(dashboard: WakeDashboard) -> WorkshopWakeDashboardView:
    """唤醒仪表盘转契约视图"""
    return WorkshopWakeDashboardView(
        group_chat_id=dashboard.group_chat_id,
        pinned_task_ids=list(dashboard.pinned_task_ids),
    )


def manual_run_to_view(result: ManualRunResult) -> WorkshopManualRunResultView:
    """手动跑工作流转契约视图"""
    return WorkshopManualRunResultView(
        task=task_to_view(result.task),
        used_light_confirmation=result.used_light_confirmation,
    )


def schedule_trigger_to_view(result: ScheduleTriggerResult) -> WorkshopScheduleTriggerResultView:
    """定时触发结果转契约视图"""
    return WorkshopScheduleTriggerResultView(
        task=task_to_view(result.task),
        run=schedule_run_to_view(result.run),
        event_ids=list(result.event_ids),
        requires_external_auth_popup=result.requires_external_auth_popup,
    )


def scheduled_completion_to_view(
    result: ScheduledCompletionResult,
) -> WorkshopScheduledCompletionResultView:
    """定时完成结果转契约视图"""
    return WorkshopScheduledCompletionResultView(
        task_status=result.task_status,
        event_ids=list(result.event_ids),
    )


def upgrade_invite_proposal_to_view(
    record: UpgradeInviteProposalRecord,
) -> WorkshopUpgradeInviteProposedView:
    """升级邀请提案记录转契约视图"""
    from app.server.workshop.domain.presets import get_preset

    experts = [
        WorkshopUpgradeInviteExpertView(key=key, name=get_preset(key).name)
        for key in record.expert_keys
    ]
    if not experts:
        raise ValueError("upgrade invite proposal requires experts")
    return WorkshopUpgradeInviteProposedView(
        proposal_id=record.id,
        conversation_id=record.conversation_id,
        expert_keys=list(record.expert_keys),
        primary_expert_key=record.primary_expert_key,
        rationale=record.rationale,
        experts=experts,
    )


def artifact_from_view(view: ArtifactSubmissionView) -> ArtifactSubmission:
    """契约产物提交转为领域值对象"""
    storage = view.storage_type
    if isinstance(storage, str):
        storage = WorkshopArtifactStorageType(storage)
    return ArtifactSubmission(
        name=view.name,
        storage_type=storage,
        content=view.content,
        storage_key=view.storage_key,
        size_bytes=view.size_bytes,
    )


def workflow_step_from_view(view: WorkshopWorkflowStepView) -> WorkflowStep:
    """契约工作流步骤转为领域值对象"""
    caps: list[WorkshopToolCapability] = []
    for item in view.external_capabilities:
        caps.append(item if isinstance(item, WorkshopToolCapability) else WorkshopToolCapability(item))
    return WorkflowStep(
        title=view.title,
        required_artifact_names=tuple(view.required_artifact_names),
        external_capabilities=tuple(caps),
    )


def capability_from_contract(value: WorkshopToolCapability | str) -> WorkshopToolCapability:
    """契约能力枚举归一为领域枚举"""
    if isinstance(value, WorkshopToolCapability):
        return value
    return WorkshopToolCapability(value)
