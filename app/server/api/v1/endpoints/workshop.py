from __future__ import annotations

from fastapi import APIRouter, Request

from app.composition import (
    chat_selected_expert_service,
    upgrade_invite_service,
    workshop_project_service,
    workshop_task_orchestrator,
    workshop_workflow_schedule_service,
)
from app.contracts.workshop import (
    WorkshopArtifactListRequest,
    WorkshopArtifactListResponse,
    WorkshopAssignTaskExpertRequest,
    WorkshopAuthorizeOperationRequest,
    WorkshopAuthorizedOperationListResponse,
    WorkshopAuthorizedOperationView,
    WorkshopBeginShopAuthView,
    WorkshopCompleteScheduledRunRequest,
    WorkshopConfirmCustomExpertRequest,
    WorkshopConfirmTaskProposalRequest,
    WorkshopConfirmWorkflowRequest,
    WorkshopCopyPresetRequest,
    WorkshopAddPresetRequest,
    WorkshopCreateProjectRequest,
    WorkshopCreateScheduleRequest,
    WorkshopCreateTaskProposalView,
    WorkshopDataSourcesView,
    WorkshopDeclineCustomExpertRequest,
    WorkshopDeclineTaskProposalRequest,
    ExpertDirectoryResponse,
    WorkshopConnectorDirectoryResponse,
    WorkshopConnectorListRequest,
    ExpertTeamDirectoryResponse,
    WorkshopUpgradeFromTeamRequest,
    WorkshopUpgradeFromExpertRequest,
    WorkshopUpgradeResultView,
    WorkshopConfirmUpgradeInviteRequest,
    WorkshopConfirmUpgradeInviteResultView,
    WorkshopDeclineUpgradeInviteRequest,
    WorkshopGetPendingUpgradeInviteRequest,
    WorkshopPendingUpgradeInviteResponse,
    WorkshopDraftWorkflowRequest,
    WorkshopEventListResponse,
    WorkshopExpertProposalIdView,
    WorkshopGrantExternalAuthRequest,
    WorkshopGroupChatIdRequest,
    WorkshopImportErrorListResponse,
    WorkshopInviteDirectoryResponse,
    WorkshopInviteExpertRequest,
    WorkshopManualRunResultView,
    WorkshopManualRunWorkflowRequest,
    WorkshopOkView,
    WorkshopPendingProposalListResponse,
    WorkshopProjectByChatResponse,
    WorkshopProjectIdRequest,
    WorkshopProjectListResponse,
    WorkshopProjectView,
    WorkshopProposeCustomExpertRequest,
    WorkshopProposeTaskRequest,
    WorkshopRecordCapabilityUseRequest,
    WorkshopRemoveExpertRequest,
    WorkshopRoomMembersResponse,
    WorkshopRosterExpertView,
    WorkshopRosterListResponse,
    WorkshopScheduleIdRequest,
    WorkshopScheduleListResponse,
    WorkshopScheduledCompletionResultView,
    WorkshopScheduleTriggerResultView,
    WorkshopScheduleView,
    WorkshopTaskAssignmentListResponse,
    WorkshopTaskIdRequest,
    WorkshopTaskListResponse,
    WorkshopTaskView,
    WorkshopTriggerScheduleRequest,
    WorkshopUnassignTaskExpertRequest,
    WorkshopUpgradeRequest,
    WorkshopUpgradeResultView,
    WorkshopWakeDashboardView,
    WorkshopWeakAcceptRequest,
    WorkshopWeakAcceptResultView,
    WorkshopWorkflowListResponse,
    WorkshopWorkflowRunListResponse,
    WorkshopListWorkflowRunsRequest,
    WorkshopWorkflowView,
)
from app.server.api.schemas import Response
from app.server.workshop.assembly import (
    artifact_from_view,
    artifact_to_view,
    authorized_operation_to_view,
    begin_shop_auth_to_view,
    capability_from_contract,
    data_sources_to_view,
    event_to_view,
    import_errors_to_view,
    invite_directory_to_view,
    manual_run_to_view,
    project_to_view,
    proposal_to_view,
    roster_expert_to_view,
    schedule_to_view,
    schedule_trigger_to_view,
    scheduled_completion_to_view,
    task_assignment_to_view,
    task_to_view,
    upgrade_invite_proposal_to_view,
    upgrade_to_view,
    wake_to_view,
    weak_accept_to_view,
    workflow_graph_from_view,
    workflow_run_to_view,
    workflow_to_view,
)
from app.server.exceptions.base import AppError
from app.server.exceptions.codes import ErrorCode
from app.server.workshop.domain.enums import WorkshopExpertKind
from app.server.workshop.persistence.repository import WorkshopRepositoryError
from app.server.workshop.services.http_errors import map_workshop_error
from app.server.workshop.services.task_orchestrator import TaskOrchestratorError
from app.server.workshop.services.workflow_schedule_service import WorkshopWorkflowScheduleError
from app.server.workshop.services.workshop_project_service import WorkshopProjectError

router = APIRouter()

_WORKSHOP_HTTP_ERRORS = (
    WorkshopProjectError,
    TaskOrchestratorError,
    WorkshopWorkflowScheduleError,
    WorkshopRepositoryError,
    KeyError,
    ValueError,
)


def _expert_kind(value: WorkshopExpertKind | str) -> WorkshopExpertKind:
    """契约专家类型归一为领域枚举"""
    if isinstance(value, WorkshopExpertKind):
        return value
    return WorkshopExpertKind(value)


@router.post("/expert/directory")
async def list_expert_directory(_request: Request) -> Response[ExpertDirectoryResponse]:
    """用户可见专家目录"""
    return Response(data=chat_selected_expert_service.list_expert_directory())


@router.post("/expert/teams")
async def list_expert_teams(_request: Request) -> Response[ExpertTeamDirectoryResponse]:
    """用户可见专家团队目录（已停用，恒为空）"""
    return Response(data=ExpertTeamDirectoryResponse(items=[]))


@router.post("/upgrade/from-team")
async def upgrade_from_team(
    _request: Request, _body: WorkshopUpgradeFromTeamRequest
) -> Response[WorkshopUpgradeResultView]:
    """团队升级已停用"""
    raise AppError(ErrorCode.INVALID_PARAMS, "不支持团队升级")


@router.post("/connectors/list")
async def list_connectors(
    request: Request, body: WorkshopConnectorListRequest
) -> Response[WorkshopConnectorDirectoryResponse]:
    """可验证连接器目录"""
    user_id: int = request.state.user_id
    data = await chat_selected_expert_service.list_connectors(
        user_id=user_id, project_id=body.project_id
    )
    return Response(data=data)


@router.post("/upgrade/from-expert")
async def upgrade_from_expert(
    request: Request, body: WorkshopUpgradeFromExpertRequest
) -> Response[WorkshopUpgradeResultView]:
    """从单专家邀请原地升级并进入房间"""
    user_id: int = request.state.user_id
    try:
        result = await chat_selected_expert_service.upgrade_from_expert(
            user_id=user_id,
            body=body,
        )
    except _WORKSHOP_HTTP_ERRORS as exc:
        raise map_workshop_error(exc) from exc
    return Response(data=result)


@router.post("/upgrade-invite/pending")
async def get_pending_upgrade_invite(
    request: Request, body: WorkshopGetPendingUpgradeInviteRequest
) -> Response[WorkshopPendingUpgradeInviteResponse]:
    """读取会话当前 pending 升级邀请；无则 proposal 为 null"""
    user_id: int = request.state.user_id
    record = await upgrade_invite_service.get_pending(
        user_id=user_id,
        conversation_id=body.conversation_id,
    )
    return Response(
        data=WorkshopPendingUpgradeInviteResponse(
            proposal=None if record is None else upgrade_invite_proposal_to_view(record)
        )
    )


@router.post("/upgrade-invite/confirm")
async def confirm_upgrade_invite(
    request: Request, body: WorkshopConfirmUpgradeInviteRequest
) -> Response[WorkshopConfirmUpgradeInviteResultView]:
    """确认 LLM 升级+邀请提议：建项、进房，返回主答专家与 Host 说明"""
    from app.agent.chat.turn.upgrade_invite_discard import (
        discard_upgrade_invite_checkpoint_after_resolution,
    )
    from app.server.chat.services.upgrade_invite import UpgradeInviteServiceError

    user_id: int = request.state.user_id
    try:
        result = await upgrade_invite_service.confirm(
            user_id=user_id,
            conversation_id=body.conversation_id,
            proposal_id=body.proposal_id,
            expert_keys=tuple(body.expert_keys),
            primary_expert_key=body.primary_expert_key,
            project_name=body.project_name,
            carried_message_count=body.carried_message_count,
        )
        await discard_upgrade_invite_checkpoint_after_resolution(
            user_id=user_id,
            conversation_id=body.conversation_id,
        )
    except UpgradeInviteServiceError as exc:
        raise AppError(ErrorCode.INVALID_PARAMS, str(exc)) from exc
    except RuntimeError as exc:
        raise AppError(ErrorCode.INTERNAL_ERROR, str(exc)) from exc
    except _WORKSHOP_HTTP_ERRORS as exc:
        raise map_workshop_error(exc) from exc
    return Response(
        data=WorkshopConfirmUpgradeInviteResultView(
            project=project_to_view(result.upgrade.project),
            primary_expert_id=result.primary_expert_id,
            host_narration=result.host_narration,
            source_user_text=result.source_user_text,
            carried_message_count=result.upgrade.carried_message_count,
        )
    )


@router.post("/upgrade-invite/decline")
async def decline_upgrade_invite(
    request: Request, body: WorkshopDeclineUpgradeInviteRequest
) -> Response[WorkshopOkView]:
    """拒绝升级+邀请提议；本会话不再主动判断"""
    from app.agent.chat.turn.upgrade_invite_discard import (
        discard_upgrade_invite_checkpoint_after_resolution,
    )
    from app.server.chat.services.upgrade_invite import UpgradeInviteServiceError

    user_id: int = request.state.user_id
    try:
        await upgrade_invite_service.decline(
            user_id=user_id,
            conversation_id=body.conversation_id,
            proposal_id=body.proposal_id,
        )
        await discard_upgrade_invite_checkpoint_after_resolution(
            user_id=user_id,
            conversation_id=body.conversation_id,
        )
    except UpgradeInviteServiceError as exc:
        raise AppError(ErrorCode.INVALID_PARAMS, str(exc)) from exc
    except RuntimeError as exc:
        raise AppError(ErrorCode.INTERNAL_ERROR, str(exc)) from exc
    return Response(data=WorkshopOkView())


@router.post("/projects/create")
async def create_workshop_project(
    request: Request, body: WorkshopCreateProjectRequest
) -> Response[WorkshopProjectView]:
    """创建工坊项目"""
    user_id: int = request.state.user_id
    try:
        project = await workshop_project_service.create_project(
            user_id=user_id,
            name=body.name,
            group_chat_id=body.group_chat_id,
            initial_expert_keys=tuple(body.initial_expert_keys),
        )
    except _WORKSHOP_HTTP_ERRORS as exc:
        raise map_workshop_error(exc) from exc
    return Response(data=project_to_view(project))


@router.post("/projects/list")
async def list_workshop_projects(
    request: Request,
) -> Response[WorkshopProjectListResponse]:
    """列出当前用户的工坊项目"""
    user_id: int = request.state.user_id
    try:
        projects = await workshop_project_service.list_projects(user_id=user_id)
    except _WORKSHOP_HTTP_ERRORS as exc:
        raise map_workshop_error(exc) from exc
    return Response(
        data=WorkshopProjectListResponse(
            items=[project_to_view(project) for project in projects]
        )
    )


@router.post("/projects/upgrade")
async def upgrade_workshop_project(
    request: Request, body: WorkshopUpgradeRequest
) -> Response[WorkshopUpgradeResultView]:
    """单 Agent 升级为工坊项目"""
    user_id: int = request.state.user_id
    try:
        result = await workshop_project_service.confirm_upgrade_to_project(
            user_id=user_id,
            group_chat_id=body.group_chat_id,
            project_name=body.project_name,
            seed_goal=body.seed_goal,
            carried_message_count=body.carried_message_count,
            initial_expert_keys=tuple(body.initial_expert_keys),
        )
    except _WORKSHOP_HTTP_ERRORS as exc:
        raise map_workshop_error(exc) from exc
    return Response(data=upgrade_to_view(result))


@router.post("/projects/get")
async def get_workshop_project(
    request: Request, body: WorkshopProjectIdRequest
) -> Response[WorkshopProjectView]:
    """读取工坊项目"""
    user_id: int = request.state.user_id
    try:
        project = await workshop_project_service.get_project(
            project_id=body.project_id, user_id=user_id
        )
    except _WORKSHOP_HTTP_ERRORS as exc:
        raise map_workshop_error(exc) from exc
    return Response(data=project_to_view(project))


@router.post("/projects/get-by-chat")
async def get_workshop_project_by_chat(
    request: Request, body: WorkshopGroupChatIdRequest
) -> Response[WorkshopProjectByChatResponse]:
    """按群聊 id 读取工坊项目；非工坊群聊返回 project=null"""
    user_id: int = request.state.user_id
    try:
        project = await workshop_project_service.get_project_by_group_chat(
            user_id=user_id, group_chat_id=body.group_chat_id
        )
    except _WORKSHOP_HTTP_ERRORS as exc:
        raise map_workshop_error(exc) from exc
    return Response(
        data=WorkshopProjectByChatResponse(
            project=project_to_view(project) if project is not None else None
        )
    )


@router.post("/projects/wake")
async def wake_workshop_project(
    request: Request, body: WorkshopProjectIdRequest
) -> Response[WorkshopWakeDashboardView]:
    """唤醒工坊仪表盘"""
    user_id: int = request.state.user_id
    try:
        dashboard = await workshop_project_service.wake_dashboard(
            project_id=body.project_id, user_id=user_id
        )
    except _WORKSHOP_HTTP_ERRORS as exc:
        raise map_workshop_error(exc) from exc
    return Response(data=wake_to_view(dashboard))


@router.post("/roster/list")
async def list_workshop_roster(
    request: Request, body: WorkshopProjectIdRequest
) -> Response[WorkshopRosterListResponse]:
    """列出工坊名册"""
    user_id: int = request.state.user_id
    try:
        experts = await workshop_project_service.list_roster(
            project_id=body.project_id, user_id=user_id
        )
    except _WORKSHOP_HTTP_ERRORS as exc:
        raise map_workshop_error(exc) from exc
    return Response(
        data=WorkshopRosterListResponse(items=[roster_expert_to_view(item) for item in experts])
    )


@router.post("/roster/invite-directory")
async def list_workshop_invite_directory(
    request: Request,
) -> Response[WorkshopInviteDirectoryResponse]:
    """列出可邀请专家目录（通用 ∪ 电商）"""
    del request
    return Response(data=invite_directory_to_view())


@router.post("/tasks/authorize-operation")
async def authorize_workshop_operation(
    request: Request, body: WorkshopAuthorizeOperationRequest
) -> Response[WorkshopAuthorizedOperationView]:
    """记录参数级 AuthorizedOperation（需已有 task external grant）"""
    user_id: int = request.state.user_id
    try:
        operation = await workshop_task_orchestrator.authorize_operation(
            project_id=body.project_id,
            user_id=user_id,
            task_id=body.task_id,
            capability=capability_from_contract(body.capability),
            operation_kind=body.operation_kind,
            payload_hash=body.payload_hash,
        )
    except _WORKSHOP_HTTP_ERRORS as exc:
        raise map_workshop_error(exc) from exc
    return Response(data=authorized_operation_to_view(operation))


@router.post("/roster/copy-preset")
async def copy_workshop_preset(
    request: Request, body: WorkshopCopyPresetRequest
) -> Response[WorkshopRosterExpertView]:
    """复制预置专家为定制专家"""
    user_id: int = request.state.user_id
    try:
        expert = await workshop_project_service.copy_preset_to_custom(
            project_id=body.project_id,
            user_id=user_id,
            preset_key=body.preset_key,
            name=body.name,
        )
    except _WORKSHOP_HTTP_ERRORS as exc:
        raise map_workshop_error(exc) from exc
    return Response(data=roster_expert_to_view(expert))


@router.post("/roster/add-preset")
async def add_workshop_preset(
    request: Request, body: WorkshopAddPresetRequest
) -> Response[WorkshopRosterExpertView]:
    """将平台预置专家加入名册（保留 preset_key）"""
    user_id: int = request.state.user_id
    try:
        expert = await workshop_project_service.add_preset_to_roster(
            project_id=body.project_id,
            user_id=user_id,
            preset_key=body.preset_key,
        )
    except _WORKSHOP_HTTP_ERRORS as exc:
        raise map_workshop_error(exc) from exc
    return Response(data=roster_expert_to_view(expert))


@router.post("/roster/propose-custom")
async def propose_custom_workshop_expert(
    request: Request, body: WorkshopProposeCustomExpertRequest
) -> Response[WorkshopExpertProposalIdView]:
    """主持提议定制专家"""
    user_id: int = request.state.user_id
    try:
        proposal_id = await workshop_project_service.host_propose_custom_expert(
            project_id=body.project_id,
            user_id=user_id,
            name=body.name,
            kind=_expert_kind(body.kind),
        )
    except _WORKSHOP_HTTP_ERRORS as exc:
        raise map_workshop_error(exc) from exc
    return Response(data=WorkshopExpertProposalIdView(proposal_id=proposal_id))


@router.post("/roster/confirm-custom")
async def confirm_custom_workshop_expert(
    request: Request, body: WorkshopConfirmCustomExpertRequest
) -> Response[WorkshopRosterExpertView]:
    """用户确认定制专家入册"""
    user_id: int = request.state.user_id
    try:
        expert = await workshop_project_service.user_confirm_custom_expert(
            project_id=body.project_id,
            user_id=user_id,
            proposal_id=body.proposal_id,
        )
    except _WORKSHOP_HTTP_ERRORS as exc:
        raise map_workshop_error(exc) from exc
    return Response(data=roster_expert_to_view(expert))


@router.post("/roster/decline-custom")
async def decline_custom_workshop_expert(
    request: Request, body: WorkshopDeclineCustomExpertRequest
) -> Response[WorkshopOkView]:
    """用户拒绝定制专家提议"""
    user_id: int = request.state.user_id
    try:
        await workshop_project_service.user_decline_custom_expert(
            project_id=body.project_id,
            user_id=user_id,
            proposal_id=body.proposal_id,
        )
    except _WORKSHOP_HTTP_ERRORS as exc:
        raise map_workshop_error(exc) from exc
    return Response(data=WorkshopOkView())


@router.post("/roster/invite")
async def invite_workshop_expert(
    request: Request, body: WorkshopInviteExpertRequest
) -> Response[WorkshopOkView]:
    """邀请专家进入房间"""
    user_id: int = request.state.user_id
    try:
        await workshop_project_service.invite_to_room(
            project_id=body.project_id, user_id=user_id, expert_id=body.expert_id
        )
    except _WORKSHOP_HTTP_ERRORS as exc:
        raise map_workshop_error(exc) from exc
    return Response(data=WorkshopOkView())


@router.post("/roster/room-members")
async def list_workshop_room_members(
    request: Request, body: WorkshopProjectIdRequest
) -> Response[WorkshopRoomMembersResponse]:
    """列出当前房间在场专家"""
    user_id: int = request.state.user_id
    try:
        members = await workshop_project_service.room_members(
            project_id=body.project_id, user_id=user_id
        )
    except _WORKSHOP_HTTP_ERRORS as exc:
        raise map_workshop_error(exc) from exc
    return Response(data=WorkshopRoomMembersResponse(expert_ids=sorted(members)))


@router.post("/roster/remove")
async def remove_workshop_expert(
    request: Request, body: WorkshopRemoveExpertRequest
) -> Response[WorkshopOkView]:
    """移出名册专家"""
    user_id: int = request.state.user_id
    try:
        await workshop_project_service.remove_from_roster(
            project_id=body.project_id, user_id=user_id, expert_id=body.expert_id
        )
    except _WORKSHOP_HTTP_ERRORS as exc:
        raise map_workshop_error(exc) from exc
    return Response(data=WorkshopOkView())


@router.post("/roster/assign-task")
async def assign_workshop_task_expert(
    request: Request, body: WorkshopAssignTaskExpertRequest
) -> Response[WorkshopOkView]:
    """将专家分配到任务"""
    user_id: int = request.state.user_id
    try:
        await workshop_project_service.assign_to_task(
            project_id=body.project_id,
            user_id=user_id,
            task_id=body.task_id,
            expert_id=body.expert_id,
        )
    except _WORKSHOP_HTTP_ERRORS as exc:
        raise map_workshop_error(exc) from exc
    return Response(data=WorkshopOkView())


@router.post("/roster/unassign-task")
async def unassign_workshop_task_expert(
    request: Request, body: WorkshopUnassignTaskExpertRequest
) -> Response[WorkshopOkView]:
    """取消任务上的专家分配"""
    user_id: int = request.state.user_id
    try:
        await workshop_project_service.kick_from_task(
            project_id=body.project_id,
            user_id=user_id,
            task_id=body.task_id,
            expert_id=body.expert_id,
        )
    except _WORKSHOP_HTTP_ERRORS as exc:
        raise map_workshop_error(exc) from exc
    return Response(data=WorkshopOkView())


@router.post("/tasks/get")
async def get_workshop_task(
    request: Request, body: WorkshopTaskIdRequest
) -> Response[WorkshopTaskView]:
    """读取工坊任务"""
    user_id: int = request.state.user_id
    try:
        task = await workshop_task_orchestrator.get(
            project_id=body.project_id, user_id=user_id, task_id=body.task_id
        )
    except _WORKSHOP_HTTP_ERRORS as exc:
        raise map_workshop_error(exc) from exc
    return Response(data=task_to_view(task))


@router.post("/tasks/list")
async def list_workshop_tasks(
    request: Request, body: WorkshopProjectIdRequest
) -> Response[WorkshopTaskListResponse]:
    """列出工坊任务"""
    user_id: int = request.state.user_id
    try:
        tasks = await workshop_task_orchestrator.list_tasks(
            project_id=body.project_id, user_id=user_id
        )
    except _WORKSHOP_HTTP_ERRORS as exc:
        raise map_workshop_error(exc) from exc
    return Response(
        data=WorkshopTaskListResponse(items=[task_to_view(task) for task in tasks])
    )


@router.post("/artifacts/list")
async def list_workshop_artifacts(
    request: Request, body: WorkshopArtifactListRequest
) -> Response[WorkshopArtifactListResponse]:
    """列出工坊产物"""
    user_id: int = request.state.user_id
    try:
        artifacts = await workshop_task_orchestrator.list_artifacts(
            project_id=body.project_id,
            user_id=user_id,
            task_id=body.task_id,
        )
    except _WORKSHOP_HTTP_ERRORS as exc:
        raise map_workshop_error(exc) from exc
    return Response(
        data=WorkshopArtifactListResponse(
            items=[artifact_to_view(item) for item in artifacts]
        )
    )


@router.post("/tasks/authorized-operations")
async def list_workshop_authorized_operations(
    request: Request, body: WorkshopProjectIdRequest
) -> Response[WorkshopAuthorizedOperationListResponse]:
    """列出项目 authorized_operations"""
    user_id: int = request.state.user_id
    try:
        operations = await workshop_task_orchestrator.list_authorized_operations(
            project_id=body.project_id, user_id=user_id
        )
    except _WORKSHOP_HTTP_ERRORS as exc:
        raise map_workshop_error(exc) from exc
    return Response(
        data=WorkshopAuthorizedOperationListResponse(
            items=[authorized_operation_to_view(item) for item in operations]
        )
    )


@router.post("/tasks/assignments")
async def list_workshop_task_assignments(
    request: Request, body: WorkshopProjectIdRequest
) -> Response[WorkshopTaskAssignmentListResponse]:
    """列出项目任务专家分配"""
    user_id: int = request.state.user_id
    try:
        assignments = await workshop_task_orchestrator.list_task_assignments(
            project_id=body.project_id, user_id=user_id
        )
    except _WORKSHOP_HTTP_ERRORS as exc:
        raise map_workshop_error(exc) from exc
    return Response(
        data=WorkshopTaskAssignmentListResponse(
            items=[task_assignment_to_view(item) for item in assignments]
        )
    )


@router.post("/ecommerce/data-sources")
async def get_workshop_ecommerce_data_sources(
    request: Request, body: WorkshopProjectIdRequest
) -> Response[WorkshopDataSourcesView]:
    """读取电商数据源状态"""
    user_id: int = request.state.user_id
    try:
        sources = await workshop_project_service.get_data_sources(
            project_id=body.project_id, user_id=user_id
        )
    except _WORKSHOP_HTTP_ERRORS as exc:
        raise map_workshop_error(exc) from exc
    return Response(data=data_sources_to_view(sources))


@router.post("/ecommerce/import-errors")
async def list_workshop_ecommerce_import_errors(
    request: Request, body: WorkshopProjectIdRequest
) -> Response[WorkshopImportErrorListResponse]:
    """读取电商导入错误"""
    user_id: int = request.state.user_id
    try:
        errors = await workshop_project_service.list_import_errors(
            project_id=body.project_id, user_id=user_id
        )
    except _WORKSHOP_HTTP_ERRORS as exc:
        raise map_workshop_error(exc) from exc
    return Response(data=import_errors_to_view(errors))


@router.post("/ecommerce/begin-shop-auth")
async def begin_workshop_ecommerce_shop_auth(
    request: Request, body: WorkshopProjectIdRequest
) -> Response[WorkshopBeginShopAuthView]:
    """发起店铺 OAuth；未配置淘宝应用时返回明确错误"""
    user_id: int = request.state.user_id
    try:
        result = await workshop_project_service.begin_shop_auth(
            project_id=body.project_id, user_id=user_id
        )
    except _WORKSHOP_HTTP_ERRORS as exc:
        raise map_workshop_error(exc) from exc
    return Response(data=begin_shop_auth_to_view(result))


@router.post("/tasks/block")
async def block_workshop_task(
    request: Request, body: WorkshopTaskIdRequest
) -> Response[WorkshopTaskView]:
    """标记任务阻塞"""
    user_id: int = request.state.user_id
    try:
        task = await workshop_task_orchestrator.mark_blocked(
            project_id=body.project_id, user_id=user_id, task_id=body.task_id
        )
    except _WORKSHOP_HTTP_ERRORS as exc:
        raise map_workshop_error(exc) from exc
    return Response(data=task_to_view(task))


@router.post("/tasks/realign")
async def realign_workshop_task(
    request: Request, body: WorkshopTaskIdRequest
) -> Response[WorkshopTaskView]:
    """任务重新对齐"""
    user_id: int = request.state.user_id
    try:
        task = await workshop_task_orchestrator.mark_re_aligning(
            project_id=body.project_id, user_id=user_id, task_id=body.task_id
        )
    except _WORKSHOP_HTTP_ERRORS as exc:
        raise map_workshop_error(exc) from exc
    return Response(data=task_to_view(task))


@router.post("/tasks/review")
async def review_workshop_task(
    request: Request, body: WorkshopTaskIdRequest
) -> Response[WorkshopTaskView]:
    """任务进入弱验收"""
    user_id: int = request.state.user_id
    try:
        task = await workshop_task_orchestrator.mark_reviewing(
            project_id=body.project_id, user_id=user_id, task_id=body.task_id
        )
    except _WORKSHOP_HTTP_ERRORS as exc:
        raise map_workshop_error(exc) from exc
    return Response(data=task_to_view(task))


@router.post("/tasks/cancel")
async def cancel_workshop_task(
    request: Request, body: WorkshopTaskIdRequest
) -> Response[WorkshopTaskView]:
    """取消任务"""
    user_id: int = request.state.user_id
    try:
        task = await workshop_task_orchestrator.cancel(
            project_id=body.project_id, user_id=user_id, task_id=body.task_id
        )
    except _WORKSHOP_HTTP_ERRORS as exc:
        raise map_workshop_error(exc) from exc
    return Response(data=task_to_view(task))


@router.post("/tasks/fail")
async def fail_workshop_task(
    request: Request, body: WorkshopTaskIdRequest
) -> Response[WorkshopTaskView]:
    """标记任务失败"""
    user_id: int = request.state.user_id
    try:
        task = await workshop_task_orchestrator.fail(
            project_id=body.project_id, user_id=user_id, task_id=body.task_id
        )
    except _WORKSHOP_HTTP_ERRORS as exc:
        raise map_workshop_error(exc) from exc
    return Response(data=task_to_view(task))


@router.post("/tasks/grant-external-auth")
async def grant_workshop_external_auth(
    request: Request, body: WorkshopGrantExternalAuthRequest
) -> Response[WorkshopTaskView]:
    """授予任务外部能力授权"""
    user_id: int = request.state.user_id
    try:
        task = await workshop_task_orchestrator.grant_external_auth(
            project_id=body.project_id,
            user_id=user_id,
            task_id=body.task_id,
            capabilities=[capability_from_contract(item) for item in body.capabilities],
        )
    except _WORKSHOP_HTTP_ERRORS as exc:
        raise map_workshop_error(exc) from exc
    return Response(data=task_to_view(task))


@router.post("/tasks/record-capability-use")
async def record_workshop_capability_use(
    request: Request, body: WorkshopRecordCapabilityUseRequest
) -> Response[WorkshopOkView]:
    """记录任务能力使用"""
    user_id: int = request.state.user_id
    try:
        await workshop_task_orchestrator.record_capability_use(
            project_id=body.project_id,
            user_id=user_id,
            task_id=body.task_id,
            capability=capability_from_contract(body.capability),
        )
    except _WORKSHOP_HTTP_ERRORS as exc:
        raise map_workshop_error(exc) from exc
    return Response(data=WorkshopOkView())


@router.post("/tasks/weak-accept")
async def weak_accept_workshop_task(
    request: Request, body: WorkshopWeakAcceptRequest
) -> Response[WorkshopWeakAcceptResultView]:
    """主持弱验收任务"""
    user_id: int = request.state.user_id
    try:
        result = await workshop_task_orchestrator.weak_accept(
            project_id=body.project_id,
            user_id=user_id,
            task_id=body.task_id,
            covered_goals=body.covered_goals,
            artifacts=[artifact_from_view(item) for item in body.artifacts],
        )
    except _WORKSHOP_HTTP_ERRORS as exc:
        raise map_workshop_error(exc) from exc
    return Response(data=weak_accept_to_view(result))


@router.post("/tasks/reject-done")
async def reject_done_workshop_task(
    request: Request, body: WorkshopTaskIdRequest
) -> Response[WorkshopTaskView]:
    """用户驳回已完成任务，回到重新对齐"""
    user_id: int = request.state.user_id
    try:
        task = await workshop_task_orchestrator.user_reject_done(
            project_id=body.project_id, user_id=user_id, task_id=body.task_id
        )
    except _WORKSHOP_HTTP_ERRORS as exc:
        raise map_workshop_error(exc) from exc
    return Response(data=task_to_view(task))


@router.post("/workflows/agent-draft")
async def agent_draft_workshop_workflow(
    request: Request, body: WorkshopDraftWorkflowRequest
) -> Response[WorkshopWorkflowView]:
    """Agent 起草工作流"""
    user_id: int = request.state.user_id
    nodes, edges, entry = workflow_graph_from_view(body.definition)
    try:
        workflow = await workshop_workflow_schedule_service.agent_draft_workflow(
            project_id=body.project_id,
            user_id=user_id,
            name=body.name,
            nodes=nodes,
            edges=edges,
            model_key=body.model_key,
            entry_node_ids=entry,
        )
    except _WORKSHOP_HTTP_ERRORS as exc:
        raise map_workshop_error(exc) from exc
    return Response(data=workflow_to_view(workflow))


@router.post("/workflows/user-draft")
async def user_draft_workshop_workflow(
    request: Request, body: WorkshopDraftWorkflowRequest
) -> Response[WorkshopWorkflowView]:
    """用户起草工作流"""
    user_id: int = request.state.user_id
    nodes, edges, entry = workflow_graph_from_view(body.definition)
    try:
        workflow = await workshop_workflow_schedule_service.user_draft_workflow(
            project_id=body.project_id,
            user_id=user_id,
            name=body.name,
            nodes=nodes,
            edges=edges,
            model_key=body.model_key,
            entry_node_ids=entry,
        )
    except _WORKSHOP_HTTP_ERRORS as exc:
        raise map_workshop_error(exc) from exc
    return Response(data=workflow_to_view(workflow))


@router.post("/workflows/confirm")
async def confirm_workshop_workflow(
    request: Request, body: WorkshopConfirmWorkflowRequest
) -> Response[WorkshopWorkflowView]:
    """确认保存工作流"""
    user_id: int = request.state.user_id
    try:
        workflow = await workshop_workflow_schedule_service.user_confirm_save_workflow(
            project_id=body.project_id,
            user_id=user_id,
            workflow_id=body.workflow_id,
        )
    except _WORKSHOP_HTTP_ERRORS as exc:
        raise map_workshop_error(exc) from exc
    return Response(data=workflow_to_view(workflow))


@router.post("/workflows/list")
async def list_workshop_workflows(
    request: Request, body: WorkshopProjectIdRequest
) -> Response[WorkshopWorkflowListResponse]:
    """列出已保存工作流"""
    user_id: int = request.state.user_id
    try:
        items = await workshop_workflow_schedule_service.list_saved_workflows(
            project_id=body.project_id, user_id=user_id
        )
    except _WORKSHOP_HTTP_ERRORS as exc:
        raise map_workshop_error(exc) from exc
    return Response(
        data=WorkshopWorkflowListResponse(items=[workflow_to_view(item) for item in items])
    )


@router.post("/workflows/runs/list")
async def list_workshop_workflow_runs(
    request: Request, body: WorkshopListWorkflowRunsRequest
) -> Response[WorkshopWorkflowRunListResponse]:
    """列出工作流运行记录"""
    user_id: int = request.state.user_id
    try:
        items = await workshop_workflow_schedule_service.list_workflow_runs(
            project_id=body.project_id,
            user_id=user_id,
            workflow_id=body.workflow_id,
            limit=body.limit,
        )
    except _WORKSHOP_HTTP_ERRORS as exc:
        raise map_workshop_error(exc) from exc
    return Response(
        data=WorkshopWorkflowRunListResponse(
            items=[workflow_run_to_view(item) for item in items]
        )
    )


@router.post("/workflows/manual-run")
async def manual_run_workshop_workflow(
    request: Request, body: WorkshopManualRunWorkflowRequest
) -> Response[WorkshopManualRunResultView]:
    """手动运行已保存工作流"""
    user_id: int = request.state.user_id
    try:
        result = await workshop_workflow_schedule_service.manual_run_with_light_confirm(
            project_id=body.project_id,
            user_id=user_id,
            workflow_id=body.workflow_id,
            authorized_capabilities=[
                capability_from_contract(item) for item in body.authorized_capabilities
            ],
        )
    except _WORKSHOP_HTTP_ERRORS as exc:
        raise map_workshop_error(exc) from exc
    return Response(data=manual_run_to_view(result))


@router.post("/schedules/create")
async def create_workshop_schedule(
    request: Request, body: WorkshopCreateScheduleRequest
) -> Response[WorkshopScheduleView]:
    """创建工坊定时"""
    user_id: int = request.state.user_id
    try:
        schedule = await workshop_workflow_schedule_service.create_schedule(
            project_id=body.project_id,
            user_id=user_id,
            workflow_id=body.workflow_id,
            cron=body.cron,
            timezone=body.timezone,
            authorized_capabilities=[
                capability_from_contract(item) for item in body.authorized_capabilities
            ],
        )
    except _WORKSHOP_HTTP_ERRORS as exc:
        raise map_workshop_error(exc) from exc
    return Response(data=schedule_to_view(schedule))


@router.post("/schedules/get")
async def get_workshop_schedule(
    request: Request, body: WorkshopScheduleIdRequest
) -> Response[WorkshopScheduleView]:
    """读取工坊定时"""
    user_id: int = request.state.user_id
    try:
        schedule = await workshop_workflow_schedule_service.get_schedule(
            project_id=body.project_id,
            user_id=user_id,
            schedule_id=body.schedule_id,
        )
    except _WORKSHOP_HTTP_ERRORS as exc:
        raise map_workshop_error(exc) from exc
    return Response(data=schedule_to_view(schedule))


@router.post("/schedules/list")
async def list_workshop_schedules(
    request: Request, body: WorkshopProjectIdRequest
) -> Response[WorkshopScheduleListResponse]:
    """列出工坊定时"""
    user_id: int = request.state.user_id
    try:
        items = await workshop_workflow_schedule_service.list_schedules(
            project_id=body.project_id, user_id=user_id
        )
    except _WORKSHOP_HTTP_ERRORS as exc:
        raise map_workshop_error(exc) from exc
    return Response(
        data=WorkshopScheduleListResponse(items=[schedule_to_view(item) for item in items])
    )


@router.post("/schedules/enable")
async def enable_workshop_schedule(
    request: Request, body: WorkshopScheduleIdRequest
) -> Response[WorkshopScheduleView]:
    """启用工坊定时并计算 next_run_at"""
    user_id: int = request.state.user_id
    try:
        schedule = await workshop_workflow_schedule_service.enable_schedule(
            project_id=body.project_id,
            user_id=user_id,
            schedule_id=body.schedule_id,
        )
    except _WORKSHOP_HTTP_ERRORS as exc:
        raise map_workshop_error(exc) from exc
    return Response(data=schedule_to_view(schedule))


@router.post("/schedules/disable")
async def disable_workshop_schedule(
    request: Request, body: WorkshopScheduleIdRequest
) -> Response[WorkshopScheduleView]:
    """禁用工坊定时并清空 next_run_at"""
    user_id: int = request.state.user_id
    try:
        schedule = await workshop_workflow_schedule_service.disable_schedule(
            project_id=body.project_id,
            user_id=user_id,
            schedule_id=body.schedule_id,
        )
    except _WORKSHOP_HTTP_ERRORS as exc:
        raise map_workshop_error(exc) from exc
    return Response(data=schedule_to_view(schedule))


@router.post("/schedules/trigger")
async def trigger_workshop_schedule(
    request: Request, body: WorkshopTriggerScheduleRequest
) -> Response[WorkshopScheduleTriggerResultView]:
    """按 trigger_key 显式触发定时"""
    user_id: int = request.state.user_id
    try:
        result = await workshop_workflow_schedule_service.trigger_schedule(
            project_id=body.project_id,
            user_id=user_id,
            schedule_id=body.schedule_id,
            trigger_key=body.trigger_key,
        )
    except _WORKSHOP_HTTP_ERRORS as exc:
        raise map_workshop_error(exc) from exc
    return Response(data=schedule_trigger_to_view(result))


@router.post("/schedules/complete")
async def complete_workshop_scheduled_run(
    request: Request, body: WorkshopCompleteScheduledRunRequest
) -> Response[WorkshopScheduledCompletionResultView]:
    """完成定时运行并落事件"""
    user_id: int = request.state.user_id
    try:
        result = await workshop_workflow_schedule_service.complete_scheduled_run(
            project_id=body.project_id,
            user_id=user_id,
            task_id=body.task_id,
            covered_goals=body.covered_goals,
            artifacts=[artifact_from_view(item) for item in body.artifacts],
        )
    except _WORKSHOP_HTTP_ERRORS as exc:
        raise map_workshop_error(exc) from exc
    return Response(data=scheduled_completion_to_view(result))


@router.post("/events/list")
async def list_workshop_events(
    request: Request, body: WorkshopProjectIdRequest
) -> Response[WorkshopEventListResponse]:
    """列出工坊事件"""
    user_id: int = request.state.user_id
    try:
        events = await workshop_workflow_schedule_service.list_events(
            project_id=body.project_id, user_id=user_id
        )
    except _WORKSHOP_HTTP_ERRORS as exc:
        raise map_workshop_error(exc) from exc
    return Response(data=WorkshopEventListResponse(items=[event_to_view(item) for item in events]))
