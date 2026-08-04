from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable, Optional, Sequence
from uuid import uuid4

from tortoise.backends.base.client import BaseDBAsyncClient
from tortoise.exceptions import IntegrityError
from tortoise.transactions import in_transaction

from app.server.workshop.domain.cron_next_fire import (
    compute_next_run_at,
    schedule_trigger_key,
)
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
from app.server.workshop.domain.ecommerce.authorized_operations import (
    AuthorizedOperation,
    append_authorized_operation,
    consume_operation,
    list_authorized_operations,
)
from app.server.workshop.domain.presets import PresetExpert
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
    WorkshopEventPayload,
    WorkshopEventRecord,
    WorkshopProjectRecord,
    WorkshopScheduleRecord,
    WorkshopScheduleRunRecord,
    WorkshopTaskRecord,
    WorkshopWorkflowRecord,
    WorkshopWorkflowRunRecord,
)
from app.server.workshop.domain.workflow_definition import (
    WorkflowEdge,
    WorkflowNode,
    definition_to_jsonable,
    parse_definition_graph,
)
from app.server.workshop.persistence.models import (
    WorkshopArtifacts,
    WorkshopEvents,
    WorkshopExpertProposals,
    WorkshopExperts,
    WorkshopProjects,
    WorkshopRoomMembers,
    WorkshopScheduleRuns,
    WorkshopSchedules,
    WorkshopTaskCapabilityUses,
    WorkshopTaskExperts,
    WorkshopTaskProposals,
    WorkshopTasks,
    WorkshopWorkflowRuns,
    WorkshopWorkflows,
)


class WorkshopRepositoryError(Exception):
    """工坊仓储错误"""


class WorkshopProjectNotFoundError(WorkshopRepositoryError):
    """工坊项目不存在或不属于当前用户"""


class WorkshopTaskNotFoundError(WorkshopRepositoryError):
    """工坊任务不存在或不属于当前用户"""


class WorkshopTaskConflictError(WorkshopRepositoryError):
    """工坊任务状态或 revision 冲突"""


class WorkshopProposalNotFoundError(WorkshopRepositoryError):
    """工坊提议不存在或状态非法"""


class WorkshopExpertNotFoundError(WorkshopRepositoryError):
    """工坊专家不存在或不属于项目"""


class WorkshopWorkflowNotFoundError(WorkshopRepositoryError):
    """工坊工作流不存在或不属于项目"""


class WorkshopScheduleNotFoundError(WorkshopRepositoryError):
    """工坊定时不存在或不属于项目"""


class WorkshopWorkflowConflictError(WorkshopRepositoryError):
    """工坊工作流状态或 revision 冲突"""


class WorkshopOwnershipError(WorkshopRepositoryError):
    """任务或资源不属于当前项目"""


class WorkshopScheduleTriggerConflictError(WorkshopRepositoryError):
    """定时触发键冲突（仅内部竞态；调用方应回读已有运行）"""


class WorkshopScheduleClaimLostError(WorkshopRepositoryError):
    """到期 claim 丢失（CAS/锁竞争）"""


class WorkshopArtifactConflictError(WorkshopRepositoryError):
    """产物名称冲突"""


class WorkshopProjectConflictError(WorkshopRepositoryError):
    """工坊项目唯一约束冲突（如群聊已绑定）"""


class WorkshopGroupChatOwnershipError(WorkshopRepositoryError):
    """工坊项目群聊归属外键失败（群聊不存在或不属于当前用户）"""


UK_WORKSHOP_PROJECTS_GROUP_CHAT = "uk_workshop_projects_group_chat"
FK_WORKSHOP_PROJECTS_GROUP_CHAT_USER = "fk_workshop_projects_group_chat_user"
UK_WORKSHOP_ROOM_MEMBERS_PROJECT_EXPERT = "uk_workshop_room_members_project_expert"
UK_WORKSHOP_TASK_EXPERTS_TASK_EXPERT = "uk_workshop_task_experts_task_expert"
UK_WORKSHOP_TASK_CAPABILITY_USES_TASK_CAP = "uk_workshop_task_capability_uses_task_cap"
UK_WORKSHOP_ARTIFACTS_TASK_NAME = "uk_workshop_artifacts_task_name"


def _constraint_name(exc: BaseException) -> str | None:
    """从 IntegrityError 链提取 PostgreSQL 约束名；无法识别则返回 None"""
    current: BaseException | None = exc
    known = (
        UK_WORKSHOP_PROJECTS_GROUP_CHAT,
        FK_WORKSHOP_PROJECTS_GROUP_CHAT_USER,
        UK_WORKSHOP_ROOM_MEMBERS_PROJECT_EXPERT,
        UK_WORKSHOP_TASK_EXPERTS_TASK_EXPERT,
        UK_WORKSHOP_TASK_CAPABILITY_USES_TASK_CAP,
        UK_WORKSHOP_ARTIFACTS_TASK_NAME,
        "uk_workshop_schedule_runs_schedule_trigger",
    )
    while current is not None:
        for attr in ("constraint_name", "constraint"):
            value = getattr(current, attr, None)
            if isinstance(value, str) and value.strip():
                return value.strip()
        lowered = str(current).lower()
        for name in known:
            if name.lower() in lowered:
                return name
        current = current.__cause__
    return None


def _is_unique_violation(exc: BaseException) -> bool:
    """判断是否唯一约束冲突"""
    current: BaseException | None = exc
    while current is not None:
        if type(current).__name__ == "UniqueViolationError":
            return True
        current = current.__cause__
    text = str(exc).lower()
    return "unique" in text or "duplicate key" in text


def _is_foreign_key_violation(exc: BaseException) -> bool:
    """判断是否外键约束冲突"""
    current: BaseException | None = exc
    while current is not None:
        if type(current).__name__ == "ForeignKeyViolationError":
            return True
        current = current.__cause__
    text = str(exc).lower()
    return "foreign key" in text or "violates foreign key" in text


def _map_project_create_integrity(exc: IntegrityError) -> WorkshopRepositoryError:
    """将项目创建 IntegrityError 映射为类型化仓储错误；未知约束 fail closed 原样抛出"""
    name = _constraint_name(exc)
    if name == UK_WORKSHOP_PROJECTS_GROUP_CHAT:
        return WorkshopProjectConflictError(
            "group chat already bound to a workshop project"
        )
    if name == FK_WORKSHOP_PROJECTS_GROUP_CHAT_USER:
        return WorkshopGroupChatOwnershipError(
            "group chat not found or not owned by user"
        )
    # 约束名缺失时按违规类型与文案做窄匹配，仍无法识别则向上抛出
    text = str(exc).lower()
    if _is_unique_violation(exc) and (
        UK_WORKSHOP_PROJECTS_GROUP_CHAT in text or "group_chat_id" in text
    ):
        return WorkshopProjectConflictError(
            "group chat already bound to a workshop project"
        )
    if _is_foreign_key_violation(exc) and (
        FK_WORKSHOP_PROJECTS_GROUP_CHAT_USER in text
        or "chat_conversations" in text
        or "group_chat" in text
    ):
        return WorkshopGroupChatOwnershipError(
            "group chat not found or not owned by user"
        )
    raise exc


def _parse_capabilities(raw: Sequence[object]) -> frozenset[WorkshopToolCapability]:
    """将持久化能力列表解析为类型化集合"""
    return frozenset(WorkshopToolCapability(str(item)) for item in raw)


def _dump_capabilities(capabilities: Iterable[WorkshopToolCapability]) -> list[str]:
    """将能力集合序列化为 JSON 列表"""
    return sorted(capability.value for capability in capabilities)


def _parse_string_list(raw: object, *, field_name: str) -> tuple[str, ...]:
    """将 JSON 字符串列表解析为元组"""
    if not isinstance(raw, list):
        raise WorkshopRepositoryError(f"{field_name} must be list")
    return tuple(str(item) for item in raw)


def _parse_definition_payload(raw: object) -> tuple[
    tuple[WorkflowNode, ...],
    tuple[WorkflowEdge, ...],
    tuple[str, ...] | None,
    str,
]:
    """解析 steps 列中的 DAG definition 文档"""
    try:
        return parse_definition_graph(raw)
    except Exception as exc:  # noqa: BLE001 — 统一仓储错误
        raise WorkshopRepositoryError(str(exc)) from exc


def _dump_definition(
    nodes: Sequence[WorkflowNode],
    edges: Sequence[WorkflowEdge],
    model_key: str,
    entry_node_ids: Sequence[str] | None = None,
) -> dict[str, object]:
    """序列化 DAG 到 JSONB"""
    return definition_to_jsonable(
        nodes=nodes,
        edges=edges,
        model_key=model_key,
        entry_node_ids=entry_node_ids,
    )


def _external_caps_from_nodes(
    nodes: Sequence[WorkflowNode],
) -> frozenset[WorkshopToolCapability]:
    """汇总节点声明的外部能力"""
    caps: set[WorkshopToolCapability] = set()
    for node in nodes:
        caps.update(node.external_capabilities)
    return frozenset(caps)


def _goals_from_nodes(nodes: Sequence[WorkflowNode]) -> tuple[str, ...]:
    """从节点标题提取目标序列"""
    return tuple(node.title for node in nodes)


def _aggregate_required_artifacts(
    nodes: Sequence[WorkflowNode],
) -> tuple[str, ...]:
    """从节点 outputs 汇总必需产物名，保序去重"""
    seen: set[str] = set()
    ordered: list[str] = []
    for node in nodes:
        for output in node.outputs:
            if not output.required or output.name in seen:
                continue
            seen.add(output.name)
            ordered.append(output.name)
    return tuple(ordered)



def _dump_event_payload(
    kind: WorkshopEventKind,
    payload: WorkshopEventPayload,
) -> dict[str, object]:
    """将类型化事件载荷序列化为 JSON"""
    if kind is WorkshopEventKind.SCHEDULE_STARTED:
        if not isinstance(payload, ScheduleStartedPayload):
            raise WorkshopRepositoryError("schedule_started payload type mismatch")
        return {
            "schedule_id": payload.schedule_id,
            "task_id": payload.task_id,
            "trigger_key": payload.trigger_key,
            "message": payload.message,
        }
    if kind is WorkshopEventKind.SCHEDULE_SUMMARY:
        if not isinstance(payload, ScheduleSummaryPayload):
            raise WorkshopRepositoryError("schedule_summary payload type mismatch")
        return {
            "task_id": payload.task_id,
            "message": payload.message,
        }
    if kind is WorkshopEventKind.ARTIFACTS_PUBLISHED:
        if not isinstance(payload, ArtifactsPublishedPayload):
            raise WorkshopRepositoryError("artifacts_published payload type mismatch")
        return {
            "task_id": payload.task_id,
            "artifact_names": list(payload.artifact_names),
        }
    if kind is WorkshopEventKind.SCHEDULE_BLOCKED:
        if not isinstance(payload, ScheduleBlockedPayload):
            raise WorkshopRepositoryError("schedule_blocked payload type mismatch")
        return {
            "task_id": payload.task_id,
            "message": payload.message,
            "reasons": list(payload.reasons),
        }
    raise WorkshopRepositoryError(f"unsupported event kind: {kind.value}")


def _parse_event_payload(
    kind: WorkshopEventKind,
    raw: object,
) -> WorkshopEventPayload:
    """将 JSON 事件载荷解析为类型化对象"""
    if not isinstance(raw, dict):
        raise WorkshopRepositoryError("event payload must be object")
    if kind is WorkshopEventKind.SCHEDULE_STARTED:
        schedule_id = raw.get("schedule_id")
        task_id = raw.get("task_id")
        trigger_key = raw.get("trigger_key")
        message = raw.get("message")
        if not isinstance(schedule_id, str):
            raise WorkshopRepositoryError("schedule_started schedule_id must be string")
        if not isinstance(task_id, str):
            raise WorkshopRepositoryError("schedule_started task_id must be string")
        if not isinstance(trigger_key, str):
            raise WorkshopRepositoryError("schedule_started trigger_key must be string")
        if not isinstance(message, str):
            raise WorkshopRepositoryError("schedule_started message must be string")
        return ScheduleStartedPayload(
            schedule_id=schedule_id,
            task_id=task_id,
            trigger_key=trigger_key,
            message=message,
        )
    if kind is WorkshopEventKind.SCHEDULE_SUMMARY:
        task_id = raw.get("task_id")
        message = raw.get("message")
        if not isinstance(task_id, str):
            raise WorkshopRepositoryError("schedule_summary task_id must be string")
        if not isinstance(message, str):
            raise WorkshopRepositoryError("schedule_summary message must be string")
        return ScheduleSummaryPayload(task_id=task_id, message=message)
    if kind is WorkshopEventKind.ARTIFACTS_PUBLISHED:
        task_id = raw.get("task_id")
        artifact_names_raw = raw.get("artifact_names")
        if not isinstance(task_id, str):
            raise WorkshopRepositoryError("artifacts_published task_id must be string")
        if not isinstance(artifact_names_raw, list):
            raise WorkshopRepositoryError("artifacts_published artifact_names must be list")
        return ArtifactsPublishedPayload(
            task_id=task_id,
            artifact_names=tuple(str(name) for name in artifact_names_raw),
        )
    if kind is WorkshopEventKind.SCHEDULE_BLOCKED:
        task_id = raw.get("task_id")
        message = raw.get("message")
        reasons_raw = raw.get("reasons")
        if not isinstance(task_id, str):
            raise WorkshopRepositoryError("schedule_blocked task_id must be string")
        if not isinstance(message, str):
            raise WorkshopRepositoryError("schedule_blocked message must be string")
        if not isinstance(reasons_raw, list):
            raise WorkshopRepositoryError("schedule_blocked reasons must be list")
        return ScheduleBlockedPayload(
            task_id=task_id,
            message=message,
            reasons=tuple(str(reason) for reason in reasons_raw),
        )
    raise WorkshopRepositoryError(f"unsupported event kind: {kind.value}")


def _artifact_row_fields(
    artifact: ArtifactSubmission,
    *,
    artifact_id: str,
) -> tuple[str, int | None, dict[str, object]]:
    """解析产物入库字段 storage_key、size_bytes、metadata；DB 键由服务端产物 id 派生"""
    if not artifact_id.strip():
        raise WorkshopRepositoryError("artifact_id required")
    if artifact.storage_type is WorkshopArtifactStorageType.DB:
        if artifact.content is None or not artifact.content.strip():
            raise WorkshopRepositoryError("db artifact content required")
        storage_key = f"db:{artifact_id.strip()}"
        metadata: dict[str, object] = {"content": artifact.content}
        if artifact.size_bytes is not None:
            size_bytes = artifact.size_bytes
        else:
            size_bytes = len(artifact.content.encode("utf-8"))
        return storage_key, size_bytes, metadata
    if artifact.storage_key is None or not artifact.storage_key.strip():
        raise WorkshopRepositoryError("external artifact storage_key required")
    if artifact.size_bytes is None or artifact.size_bytes <= 0:
        raise WorkshopRepositoryError("external artifact size_bytes required")
    if artifact.content is not None:
        raise WorkshopRepositoryError("external artifact must not carry inline content")
    return artifact.storage_key.strip(), artifact.size_bytes, {}


def _build_project_brief(*, bootstrap_keys: Sequence[str] | None = None) -> dict[str, object]:
    """组装项目 brief；仅记录 bootstrap_keys（遗留 brief.pack 字段忽略）"""
    brief: dict[str, object] = {}
    if bootstrap_keys:
        brief["bootstrap_keys"] = list(bootstrap_keys)
    return brief


def _to_project(row: WorkshopProjects) -> WorkshopProjectRecord:
    """ORM 项目行转读模型"""
    return WorkshopProjectRecord(
        id=row.id,
        user_id=int(row.user_id),
        name=row.name,
        group_chat_id=int(row.group_chat_id),
        host_role=WorkshopRole.HOST,
    )


def _to_expert(row: WorkshopExperts) -> RosterExpert:
    """ORM 专家行转读模型"""
    return RosterExpert(
        id=row.id,
        project_id=row.project_id,
        name=row.name,
        kind=WorkshopExpertKind(row.kind),
        preset_key=row.preset_key,
        source_preset_key=row.source_preset_key,
    )


def _to_task(row: WorkshopTasks) -> WorkshopTaskRecord:
    """ORM 任务行转读模型"""
    return WorkshopTaskRecord(
        id=row.id,
        project_id=row.project_id,
        title=row.title,
        goals=_parse_string_list(row.goals, field_name="task goals"),
        required_artifacts=_parse_string_list(
            row.required_artifacts, field_name="task required_artifacts"
        ),
        status=WorkshopTaskStatus(row.status),
        schedule_id=row.schedule_id,
        schedule_authorized=bool(row.schedule_authorized),
        external_auth=_parse_capabilities(row.external_auth),
        revision=int(row.revision),
    )


def _to_workflow(row: WorkshopWorkflows) -> WorkshopWorkflowRecord:
    """ORM 工作流行转读模型"""
    nodes, edges, entry, model_key = _parse_definition_payload(row.steps)
    return WorkshopWorkflowRecord(
        id=row.id,
        project_id=row.project_id,
        name=row.name,
        nodes=nodes,
        edges=edges,
        status=WorkshopWorkflowStatus(row.status),
        source=WorkshopWorkflowSource(row.source),
        revision=int(row.revision),
        model_key=model_key,
        entry_node_ids=entry,
    )


def _to_workflow_run(row: WorkshopWorkflowRuns) -> WorkshopWorkflowRunRecord:
    """ORM 运行记录转读模型"""
    return WorkshopWorkflowRunRecord(
        id=row.id,
        project_id=row.project_id,
        workflow_id=row.workflow_id,
        workflow_revision=int(row.workflow_revision),
        schedule_id=row.schedule_id,
        trigger=WorkshopWorkflowRunTrigger(row.trigger),
        status=WorkshopWorkflowRunStatus(row.status),
        current_node_id=row.current_node_id,
        error_message=row.error_message,
        started_at=row.started_at,
        finished_at=row.finished_at,
        created_at=row.created_at,
        revision=int(row.revision),
    )


def _to_schedule(row: WorkshopSchedules) -> WorkshopScheduleRecord:
    """ORM 定时行转读模型"""
    return WorkshopScheduleRecord(
        id=row.id,
        project_id=row.project_id,
        workflow_id=row.workflow_id,
        cron=row.cron,
        timezone=row.timezone,
        enabled=bool(row.enabled),
        authorized_at=row.authorized_at,
        authorized_external_capabilities=_parse_capabilities(
            row.authorized_external_capabilities
        ),
        next_run_at=row.next_run_at,
    )


def _to_event(row: WorkshopEvents) -> WorkshopEventRecord:
    """ORM 事件行转读模型"""
    kind = WorkshopEventKind(row.kind)
    return WorkshopEventRecord(
        event_key=row.event_key,
        project_id=row.project_id,
        task_id=row.task_id,
        kind=kind,
        payload=_parse_event_payload(kind, row.payload),
        created_at=row.created_at,
    )


def _to_task_proposal(row: WorkshopTaskProposals) -> CreateTaskProposal:
    """ORM 立任务提议转读模型"""
    return CreateTaskProposal(
        id=row.id,
        project_id=row.project_id,
        title=row.title,
        goals=_parse_string_list(row.goals, field_name="task proposal goals"),
        required_artifacts=_parse_string_list(
            row.required_artifacts, field_name="task proposal required_artifacts"
        ),
        status=WorkshopProposalStatus(row.status),
    )


def _to_schedule_run(row: WorkshopScheduleRuns) -> WorkshopScheduleRunRecord:
    """ORM 定时运行行转读模型"""
    return WorkshopScheduleRunRecord(
        id=row.id,
        project_id=row.project_id,
        schedule_id=row.schedule_id,
        trigger_key=row.trigger_key,
        task_id=row.task_id,
    )


def _artifact_content_from_metadata(
    storage_type: WorkshopArtifactStorageType,
    metadata: object,
) -> str | None:
    """DB 存储产物从 metadata 读取 inline content"""
    if storage_type is not WorkshopArtifactStorageType.DB:
        return None
    if not isinstance(metadata, dict):
        return None
    content = metadata.get("content")
    if content is None:
        return None
    if not isinstance(content, str):
        raise WorkshopRepositoryError("artifact metadata content must be string")
    return content


def _to_artifact(row: WorkshopArtifacts) -> ArtifactRecord:
    """ORM 产物行转读模型"""
    storage_type = WorkshopArtifactStorageType(row.storage_type)
    return ArtifactRecord(
        id=row.id,
        project_id=row.project_id,
        task_id=row.task_id,
        name=row.name,
        storage_type=storage_type,
        storage_key=row.storage_key,
        size_bytes=int(row.size_bytes) if row.size_bytes is not None else None,
        content=_artifact_content_from_metadata(storage_type, row.metadata),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class WorkshopRepository:
    """工坊产品状态持久化仓储"""

    async def _assert_task_in_project(
        self,
        *,
        project_id: str,
        task_id: str,
        connection: BaseDBAsyncClient | None = None,
    ) -> None:
        """校验任务属于指定项目"""
        queryset = WorkshopTasks.filter(id=task_id, project_id=project_id)
        if connection is not None:
            queryset = queryset.using_db(connection)
        if not await queryset.exists():
            raise WorkshopOwnershipError(task_id)

    async def _insert_artifacts(
        self,
        *,
        project_id: str,
        task_id: str,
        artifacts: Sequence[ArtifactSubmission],
        connection: BaseDBAsyncClient,
    ) -> None:
        """在事务内按 (task_id,name) 原子写入或替换产物；不触碰能力审计"""
        for artifact in artifacts:
            name = artifact.name.strip()
            existing = (
                await WorkshopArtifacts.filter(task_id=task_id, name=name)
                .using_db(connection)
                .first()
            )
            if existing is not None:
                storage_key, size_bytes, metadata = _artifact_row_fields(
                    artifact, artifact_id=existing.id
                )
                await WorkshopArtifacts.filter(id=existing.id).using_db(connection).update(
                    storage_type=artifact.storage_type.value,
                    storage_key=storage_key,
                    size_bytes=size_bytes,
                    metadata=metadata,
                )
                continue
            artifact_id = new_id("art")
            storage_key, size_bytes, metadata = _artifact_row_fields(
                artifact, artifact_id=artifact_id
            )
            try:
                await WorkshopArtifacts.create(
                    id=artifact_id,
                    project_id=project_id,
                    task_id=task_id,
                    name=name,
                    storage_type=artifact.storage_type.value,
                    storage_key=storage_key,
                    size_bytes=size_bytes,
                    metadata=metadata,
                    using_db=connection,
                )
            except IntegrityError as exc:
                if (
                    _constraint_name(exc) == UK_WORKSHOP_ARTIFACTS_TASK_NAME
                    or _is_unique_violation(exc)
                ):
                    raise WorkshopArtifactConflictError(name) from exc
                raise

    async def _load_workflow_row(
        self,
        *,
        project_id: str,
        workflow_id: str,
        connection: BaseDBAsyncClient,
        require_saved: bool,
    ) -> WorkshopWorkflows:
        """加载工作流行并可选校验已保存"""
        row = (
            await WorkshopWorkflows.filter(id=workflow_id, project_id=project_id)
            .using_db(connection)
            .first()
        )
        if row is None:
            raise WorkshopWorkflowNotFoundError(workflow_id)
        if require_saved and row.status != WorkshopWorkflowStatus.SAVED.value:
            raise WorkshopWorkflowNotFoundError(workflow_id)
        return row

    async def create_project_with_presets(
        self,
        *,
        project_id: str,
        user_id: int,
        name: str,
        group_chat_id: int,
        presets: Sequence[PresetExpert] | None = None,
        bootstrap_keys: Sequence[str] | None = None,
    ) -> WorkshopProjectRecord:
        """在一个事务内创建项目及预置专家"""
        roster_presets = tuple(presets) if presets is not None else ()
        try:
            async with in_transaction() as connection:
                project = await WorkshopProjects.create(
                    id=project_id,
                    user_id=user_id,
                    name=name,
                    group_chat_id=group_chat_id,
                    brief=_build_project_brief(bootstrap_keys=bootstrap_keys),
                    using_db=connection,
                )
                await WorkshopExperts.bulk_create(
                    [
                        WorkshopExperts(
                            id=_preset_expert_id(project_id, preset.key),
                            project_id=project_id,
                            name=preset.name,
                            kind=preset.kind.value,
                            preset_key=preset.key,
                            source_preset_key=None,
                        )
                        for preset in roster_presets
                    ],
                    using_db=connection,
                )
        except IntegrityError as exc:
            raise _map_project_create_integrity(exc) from exc
        return _to_project(project)

    async def create_project_with_presets_and_task_proposal(
        self,
        *,
        project_id: str,
        user_id: int,
        name: str,
        group_chat_id: int,
        proposal_id: str,
        proposal_title: str,
        proposal_goals: Sequence[str],
        proposal_required_artifacts: Sequence[str] = (),
        presets: Sequence[PresetExpert] | None = None,
        bootstrap_keys: Sequence[str] | None = None,
    ) -> tuple[WorkshopProjectRecord, CreateTaskProposal]:
        """在一个事务内创建项目、预置专家与待确认立任务提议"""
        roster_presets = tuple(presets) if presets is not None else ()
        try:
            async with in_transaction() as connection:
                project = await WorkshopProjects.create(
                    id=project_id,
                    user_id=user_id,
                    name=name,
                    group_chat_id=group_chat_id,
                    brief=_build_project_brief(bootstrap_keys=bootstrap_keys),
                    using_db=connection,
                )
                await WorkshopExperts.bulk_create(
                    [
                        WorkshopExperts(
                            id=_preset_expert_id(project_id, preset.key),
                            project_id=project_id,
                            name=preset.name,
                            kind=preset.kind.value,
                            preset_key=preset.key,
                            source_preset_key=None,
                        )
                        for preset in roster_presets
                    ],
                    using_db=connection,
                )
                proposal_row = await WorkshopTaskProposals.create(
                    id=proposal_id,
                    project_id=project_id,
                    title=proposal_title,
                    goals=list(proposal_goals),
                    required_artifacts=list(proposal_required_artifacts),
                    status=WorkshopProposalStatus.PENDING.value,
                    using_db=connection,
                )
        except IntegrityError as exc:
            raise _map_project_create_integrity(exc) from exc
        return _to_project(project), _to_task_proposal(proposal_row)

    async def get_project_brief(
        self,
        *,
        project_id: str,
        user_id: int,
    ) -> dict[str, object]:
        """读取项目 brief JSON"""
        row = await WorkshopProjects.filter(id=project_id, user_id=user_id).first()
        if row is None:
            raise WorkshopProjectNotFoundError(project_id)
        brief = row.brief
        if not isinstance(brief, dict):
            return {}
        return dict(brief)

    async def update_project_brief(
        self,
        *,
        project_id: str,
        user_id: int,
        brief: dict[str, object],
    ) -> None:
        """整体写入项目 brief JSON"""
        updated = await WorkshopProjects.filter(id=project_id, user_id=user_id).update(
            brief=brief
        )
        if updated != 1:
            raise WorkshopProjectNotFoundError(project_id)

    async def list_authorized_operations_for_project(
        self,
        *,
        project_id: str,
        user_id: int,
    ) -> tuple[AuthorizedOperation, ...]:
        """读取项目 authorized_operations"""
        brief = await self.get_project_brief(project_id=project_id, user_id=user_id)
        return list_authorized_operations(brief)

    async def append_authorized_operation_for_project(
        self,
        *,
        project_id: str,
        user_id: int,
        operation: AuthorizedOperation,
    ) -> None:
        """追加 authorized_operation 到项目 brief"""
        brief = await self.get_project_brief(project_id=project_id, user_id=user_id)
        updated = append_authorized_operation(brief, operation)
        await self.update_project_brief(
            project_id=project_id, user_id=user_id, brief=updated
        )

    async def consume_authorized_operation_for_project(
        self,
        *,
        project_id: str,
        user_id: int,
        operation_id: str,
        consumed_at: datetime,
    ) -> None:
        """标记 authorized_operation 已消费"""
        brief = await self.get_project_brief(project_id=project_id, user_id=user_id)
        updated = consume_operation(
            brief, operation_id=operation_id, consumed_at=consumed_at
        )
        await self.update_project_brief(
            project_id=project_id, user_id=user_id, brief=updated
        )

    async def get_project(
        self,
        *,
        project_id: str,
        user_id: int,
    ) -> WorkshopProjectRecord | None:
        """按所有权读取工坊项目"""
        row = await WorkshopProjects.filter(id=project_id, user_id=user_id).first()
        if row is None:
            return None
        return _to_project(row)

    async def get_project_owner_user_id(self, *, project_id: str) -> int:
        """按项目 id 读取所有者 user_id（定时派发用）"""
        row = await WorkshopProjects.filter(id=project_id).first()
        if row is None:
            raise WorkshopProjectNotFoundError(project_id)
        return int(row.user_id)

    async def get_project_by_group_chat(
        self,
        *,
        user_id: int,
        group_chat_id: int,
    ) -> WorkshopProjectRecord | None:
        """按群聊 id 与所有权读取工坊项目"""
        row = await WorkshopProjects.filter(
            user_id=user_id, group_chat_id=group_chat_id
        ).first()
        if row is None:
            return None
        return _to_project(row)

    async def require_project(
        self,
        *,
        project_id: str,
        user_id: int,
    ) -> WorkshopProjectRecord:
        """按所有权读取工坊项目；缺失则失败"""
        project = await self.get_project(project_id=project_id, user_id=user_id)
        if project is None:
            raise WorkshopProjectNotFoundError(project_id)
        return project

    async def list_projects_for_user(
        self,
        *,
        user_id: int,
    ) -> list[WorkshopProjectRecord]:
        """按 updated_at 降序列出用户工坊项目"""
        rows = await WorkshopProjects.filter(user_id=user_id).order_by(
            "-updated_at", "-id"
        )
        return [_to_project(row) for row in rows]

    async def list_tasks_for_project(
        self,
        *,
        project_id: str,
        user_id: int,
    ) -> list[WorkshopTaskRecord]:
        """按 created_at 列出项目全部任务"""
        await self.require_project(project_id=project_id, user_id=user_id)
        rows = await WorkshopTasks.filter(project_id=project_id).order_by(
            "created_at", "id"
        )
        return [_to_task(row) for row in rows]

    async def list_artifacts_for_project(
        self,
        *,
        project_id: str,
        user_id: int,
        task_id: str | None = None,
    ) -> list[ArtifactRecord]:
        """列出项目产物，可按 task_id 过滤"""
        await self.require_project(project_id=project_id, user_id=user_id)
        queryset = WorkshopArtifacts.filter(project_id=project_id)
        if task_id is not None:
            await self._assert_task_in_project(
                project_id=project_id, task_id=task_id
            )
            queryset = queryset.filter(task_id=task_id)
        rows = await queryset.order_by("created_at", "id")
        return [_to_artifact(row) for row in rows]

    async def list_pending_task_proposals(
        self,
        *,
        project_id: str,
        user_id: int,
    ) -> list[CreateTaskProposal]:
        """列出待确认立任务提议"""
        await self.require_project(project_id=project_id, user_id=user_id)
        rows = await WorkshopTaskProposals.filter(
            project_id=project_id,
            status=WorkshopProposalStatus.PENDING.value,
        ).order_by("created_at", "id")
        return [_to_task_proposal(row) for row in rows]

    async def list_task_assignments_for_project(
        self,
        *,
        project_id: str,
        user_id: int,
    ) -> list[TaskAssignmentRecord]:
        """列出项目全部任务的专家分配"""
        await self.require_project(project_id=project_id, user_id=user_id)
        rows = await WorkshopTaskExperts.filter(project_id=project_id).order_by(
            "task_id", "expert_id"
        )
        grouped: dict[str, list[str]] = {}
        for row in rows:
            grouped.setdefault(row.task_id, []).append(row.expert_id)
        return [
            TaskAssignmentRecord(task_id=task_id, expert_ids=tuple(expert_ids))
            for task_id, expert_ids in sorted(grouped.items())
        ]

    async def list_roster(
        self,
        *,
        project_id: str,
        user_id: int,
    ) -> list[RosterExpert]:
        """按项目所有权列出专家名册"""
        await self.require_project(project_id=project_id, user_id=user_id)
        rows = await WorkshopExperts.filter(project_id=project_id).order_by(
            "created_at", "id"
        )
        return [_to_expert(row) for row in rows]

    async def get_expert(
        self,
        *,
        project_id: str,
        user_id: int,
        expert_id: str,
    ) -> RosterExpert:
        """按项目所有权读取名册专家"""
        await self.require_project(project_id=project_id, user_id=user_id)
        row = await WorkshopExperts.filter(
            id=expert_id, project_id=project_id
        ).first()
        if row is None:
            raise WorkshopExpertNotFoundError(expert_id)
        return _to_expert(row)

    async def add_custom_expert(
        self,
        *,
        project_id: str,
        user_id: int,
        expert_id: str,
        name: str,
        kind: WorkshopExpertKind,
        source_preset_key: Optional[str],
    ) -> RosterExpert:
        """向名册写入定制专家"""
        await self.require_project(project_id=project_id, user_id=user_id)
        row = await WorkshopExperts.create(
            id=expert_id,
            project_id=project_id,
            name=name,
            kind=kind.value,
            preset_key=None,
            source_preset_key=source_preset_key,
        )
        return _to_expert(row)

    async def add_preset_expert(
        self,
        *,
        project_id: str,
        user_id: int,
        preset: PresetExpert,
    ) -> RosterExpert:
        """将平台预置专家写入名册；幂等"""
        await self.require_project(project_id=project_id, user_id=user_id)
        expert_id = _preset_expert_id(project_id, preset.key)
        existing = await WorkshopExperts.get_or_none(id=expert_id, project_id=project_id)
        if existing is not None:
            return _to_expert(existing)
        try:
            row = await WorkshopExperts.create(
                id=expert_id,
                project_id=project_id,
                name=preset.name,
                kind=preset.kind.value,
                preset_key=preset.key,
                source_preset_key=None,
            )
        except IntegrityError:
            row = await WorkshopExperts.get(id=expert_id, project_id=project_id)
        return _to_expert(row)

    async def create_expert_proposal(
        self,
        *,
        proposal_id: str,
        project_id: str,
        user_id: int,
        name: str,
        kind: WorkshopExpertKind,
        source_preset_key: Optional[str] = None,
    ) -> str:
        """创建待确认的定制专家提议"""
        await self.require_project(project_id=project_id, user_id=user_id)
        await WorkshopExpertProposals.create(
            id=proposal_id,
            project_id=project_id,
            name=name,
            kind=kind.value,
            source_preset_key=source_preset_key,
            status=WorkshopProposalStatus.PENDING.value,
        )
        return proposal_id

    async def confirm_expert_proposal(
        self,
        *,
        project_id: str,
        user_id: int,
        proposal_id: str,
        expert_id: str,
    ) -> RosterExpert:
        """确认定制专家提议并写入名册"""
        await self.require_project(project_id=project_id, user_id=user_id)
        async with in_transaction() as connection:
            proposal = await WorkshopExpertProposals.filter(
                id=proposal_id,
                project_id=project_id,
                status=WorkshopProposalStatus.PENDING.value,
            ).using_db(connection).first()
            if proposal is None:
                raise WorkshopProposalNotFoundError(proposal_id)
            updated = await WorkshopExpertProposals.filter(
                id=proposal_id,
                project_id=project_id,
                status=WorkshopProposalStatus.PENDING.value,
            ).using_db(connection).update(
                status=WorkshopProposalStatus.CONFIRMED.value
            )
            if updated != 1:
                raise WorkshopProposalNotFoundError(proposal_id)
            expert = await WorkshopExperts.create(
                id=expert_id,
                project_id=project_id,
                name=proposal.name,
                kind=proposal.kind,
                preset_key=None,
                source_preset_key=proposal.source_preset_key,
                using_db=connection,
            )
        return _to_expert(expert)

    async def decline_expert_proposal(
        self,
        *,
        project_id: str,
        user_id: int,
        proposal_id: str,
    ) -> None:
        """拒绝定制专家提议"""
        await self.require_project(project_id=project_id, user_id=user_id)
        updated = await WorkshopExpertProposals.filter(
            id=proposal_id,
            project_id=project_id,
            status=WorkshopProposalStatus.PENDING.value,
        ).update(status=WorkshopProposalStatus.DECLINED.value)
        if updated != 1:
            raise WorkshopProposalNotFoundError(proposal_id)

    async def remove_expert(
        self,
        *,
        project_id: str,
        user_id: int,
        expert_id: str,
    ) -> None:
        """移出名册并清理任务分配与房间成员"""
        await self.get_expert(
            project_id=project_id, user_id=user_id, expert_id=expert_id
        )
        async with in_transaction() as connection:
            await WorkshopTaskExperts.filter(
                project_id=project_id, expert_id=expert_id
            ).using_db(connection).delete()
            await WorkshopRoomMembers.filter(
                project_id=project_id, expert_id=expert_id
            ).using_db(connection).delete()
            deleted = await WorkshopExperts.filter(
                id=expert_id, project_id=project_id
            ).using_db(connection).delete()
            if deleted != 1:
                raise WorkshopExpertNotFoundError(expert_id)

    async def add_room_member(
        self,
        *,
        project_id: str,
        user_id: int,
        expert_id: str,
    ) -> None:
        """将名册专家加入当前房间；并发唯一冲突视为幂等成功"""
        await self.get_expert(
            project_id=project_id, user_id=user_id, expert_id=expert_id
        )
        try:
            await WorkshopRoomMembers.create(
                project_id=project_id,
                expert_id=expert_id,
            )
        except IntegrityError as exc:
            if _constraint_name(exc) == UK_WORKSHOP_ROOM_MEMBERS_PROJECT_EXPERT:
                return
            raise

    async def list_room_members(
        self,
        *,
        project_id: str,
        user_id: int,
    ) -> set[str]:
        """列出当前房间在场专家 id"""
        await self.require_project(project_id=project_id, user_id=user_id)
        rows = await WorkshopRoomMembers.filter(project_id=project_id).all()
        return {row.expert_id for row in rows}

    async def assign_task_expert(
        self,
        *,
        project_id: str,
        user_id: int,
        task_id: str,
        expert_id: str,
    ) -> None:
        """将名册专家分配到任务；并发唯一冲突视为幂等成功"""
        await self.get_expert(
            project_id=project_id, user_id=user_id, expert_id=expert_id
        )
        await self.require_task(
            project_id=project_id, user_id=user_id, task_id=task_id
        )
        try:
            await WorkshopTaskExperts.create(
                project_id=project_id,
                task_id=task_id,
                expert_id=expert_id,
            )
        except IntegrityError as exc:
            if _constraint_name(exc) == UK_WORKSHOP_TASK_EXPERTS_TASK_EXPERT:
                return
            raise

    async def unassign_task_expert(
        self,
        *,
        project_id: str,
        user_id: int,
        task_id: str,
        expert_id: str,
    ) -> None:
        """任务离场，不影响名册"""
        await self.require_task(
            project_id=project_id, user_id=user_id, task_id=task_id
        )
        await WorkshopTaskExperts.filter(
            project_id=project_id, task_id=task_id, expert_id=expert_id
        ).delete()

    async def list_task_experts(
        self,
        *,
        project_id: str,
        user_id: int,
        task_id: str,
    ) -> set[str]:
        """查询任务上的专家分配"""
        await self.require_task(
            project_id=project_id, user_id=user_id, task_id=task_id
        )
        rows = await WorkshopTaskExperts.filter(
            project_id=project_id, task_id=task_id
        ).all()
        return {row.expert_id for row in rows}

    async def create_task_proposal(
        self,
        *,
        proposal_id: str,
        project_id: str,
        user_id: int,
        title: str,
        goals: Sequence[str],
        required_artifacts: Sequence[str] = (),
    ) -> CreateTaskProposal:
        """创建待确认立任务提议"""
        await self.require_project(project_id=project_id, user_id=user_id)
        row = await WorkshopTaskProposals.create(
            id=proposal_id,
            project_id=project_id,
            title=title,
            goals=list(goals),
            required_artifacts=list(required_artifacts),
            status=WorkshopProposalStatus.PENDING.value,
        )
        return _to_task_proposal(row)

    async def get_task_proposal(
        self,
        *,
        project_id: str,
        user_id: int,
        proposal_id: str,
    ) -> CreateTaskProposal:
        """读取立任务提议"""
        await self.require_project(project_id=project_id, user_id=user_id)
        row = await WorkshopTaskProposals.filter(
            id=proposal_id, project_id=project_id
        ).first()
        if row is None:
            raise WorkshopProposalNotFoundError(proposal_id)
        return _to_task_proposal(row)

    async def confirm_task_proposal(
        self,
        *,
        project_id: str,
        user_id: int,
        proposal_id: str,
        task_id: str,
    ) -> WorkshopTaskRecord:
        """确认立任务提议并创建 aligning 任务"""
        await self.require_project(project_id=project_id, user_id=user_id)
        async with in_transaction() as connection:
            proposal = await WorkshopTaskProposals.filter(
                id=proposal_id,
                project_id=project_id,
                status=WorkshopProposalStatus.PENDING.value,
            ).using_db(connection).first()
            if proposal is None:
                raise WorkshopProposalNotFoundError(proposal_id)
            updated = await WorkshopTaskProposals.filter(
                id=proposal_id,
                project_id=project_id,
                status=WorkshopProposalStatus.PENDING.value,
            ).using_db(connection).update(
                status=WorkshopProposalStatus.CONFIRMED.value
            )
            if updated != 1:
                raise WorkshopProposalNotFoundError(proposal_id)
            goals_raw = proposal.goals
            if not isinstance(goals_raw, list):
                raise WorkshopRepositoryError("task proposal goals must be list")
            required_raw = proposal.required_artifacts
            if not isinstance(required_raw, list):
                raise WorkshopRepositoryError(
                    "task proposal required_artifacts must be list"
                )
            task = await WorkshopTasks.create(
                id=task_id,
                project_id=project_id,
                title=proposal.title,
                goals=list(goals_raw),
                required_artifacts=list(required_raw),
                status=WorkshopTaskStatus.ALIGNING.value,
                schedule_id=None,
                schedule_authorized=False,
                external_auth=[],
                revision=1,
                using_db=connection,
            )
        return _to_task(task)

    async def decline_task_proposal(
        self,
        *,
        project_id: str,
        user_id: int,
        proposal_id: str,
    ) -> None:
        """拒绝立任务提议"""
        await self.require_project(project_id=project_id, user_id=user_id)
        updated = await WorkshopTaskProposals.filter(
            id=proposal_id,
            project_id=project_id,
            status=WorkshopProposalStatus.PENDING.value,
        ).update(status=WorkshopProposalStatus.DECLINED.value)
        if updated != 1:
            raise WorkshopProposalNotFoundError(proposal_id)

    async def create_task(
        self,
        *,
        task_id: str,
        project_id: str,
        user_id: int,
        title: str,
        goals: Sequence[str],
        status: WorkshopTaskStatus,
        required_artifacts: Sequence[str] = (),
        schedule_id: Optional[str] = None,
        schedule_authorized: bool = False,
        external_auth: Iterable[WorkshopToolCapability] = (),
    ) -> WorkshopTaskRecord:
        """按项目所有权创建任务"""
        await self.require_project(project_id=project_id, user_id=user_id)
        if schedule_id is not None:
            await self.get_schedule(
                project_id=project_id, user_id=user_id, schedule_id=schedule_id
            )
        row = await WorkshopTasks.create(
            id=task_id,
            project_id=project_id,
            title=title,
            goals=list(goals),
            required_artifacts=list(required_artifacts),
            status=status.value,
            schedule_id=schedule_id,
            schedule_authorized=schedule_authorized,
            external_auth=_dump_capabilities(external_auth),
            revision=1,
        )
        return _to_task(row)

    async def get_task(
        self,
        *,
        project_id: str,
        user_id: int,
        task_id: str,
    ) -> WorkshopTaskRecord | None:
        """按项目所有权读取任务"""
        await self.require_project(project_id=project_id, user_id=user_id)
        row = await WorkshopTasks.filter(
            id=task_id, project_id=project_id
        ).first()
        if row is None:
            return None
        return _to_task(row)

    async def require_task(
        self,
        *,
        project_id: str,
        user_id: int,
        task_id: str,
    ) -> WorkshopTaskRecord:
        """按项目所有权读取任务；缺失则失败"""
        task = await self.get_task(
            project_id=project_id, user_id=user_id, task_id=task_id
        )
        if task is None:
            raise WorkshopTaskNotFoundError(task_id)
        return task

    async def list_tasks_by_statuses(
        self,
        *,
        project_id: str,
        user_id: int,
        statuses: Sequence[WorkshopTaskStatus],
    ) -> list[WorkshopTaskRecord]:
        """按状态列出项目任务"""
        await self.require_project(project_id=project_id, user_id=user_id)
        rows = await WorkshopTasks.filter(
            project_id=project_id,
            status__in=[status.value for status in statuses],
        ).order_by("created_at", "id")
        return [_to_task(row) for row in rows]

    async def update_task_status_cas(
        self,
        *,
        project_id: str,
        user_id: int,
        task_id: str,
        expected_revision: int,
        expected_status: WorkshopTaskStatus,
        target_status: WorkshopTaskStatus,
        connection: BaseDBAsyncClient | None = None,
    ) -> WorkshopTaskRecord:
        """按所有权、revision 与旧状态 CAS 更新任务状态"""
        await self.require_project(project_id=project_id, user_id=user_id)
        queryset = WorkshopTasks.filter(
            id=task_id,
            project_id=project_id,
            revision=expected_revision,
            status=expected_status.value,
        )
        if connection is not None:
            queryset = queryset.using_db(connection)
        updated = await queryset.update(
            status=target_status.value,
            revision=expected_revision + 1,
        )
        if updated != 1:
            raise WorkshopTaskConflictError(task_id)
        if connection is not None:
            row = (
                await WorkshopTasks.filter(id=task_id, project_id=project_id)
                .using_db(connection)
                .first()
            )
            if row is None:
                raise WorkshopTaskNotFoundError(task_id)
            return _to_task(row)
        return await self.require_task(
            project_id=project_id, user_id=user_id, task_id=task_id
        )

    async def update_task_external_auth_cas(
        self,
        *,
        project_id: str,
        user_id: int,
        task_id: str,
        expected_revision: int,
        expected_status: WorkshopTaskStatus,
        external_auth: Iterable[WorkshopToolCapability],
    ) -> WorkshopTaskRecord:
        """按 CAS 条件写入任务外部授权集合"""
        await self.require_project(project_id=project_id, user_id=user_id)
        updated = await WorkshopTasks.filter(
            id=task_id,
            project_id=project_id,
            revision=expected_revision,
            status=expected_status.value,
        ).update(
            external_auth=_dump_capabilities(external_auth),
            revision=expected_revision + 1,
        )
        if updated != 1:
            raise WorkshopTaskConflictError(task_id)
        return await self.require_task(
            project_id=project_id, user_id=user_id, task_id=task_id
        )

    async def create_workflow(
        self,
        *,
        workflow_id: str,
        project_id: str,
        user_id: int,
        name: str,
        nodes: Sequence[WorkflowNode],
        edges: Sequence[WorkflowEdge],
        model_key: str,
        status: WorkshopWorkflowStatus,
        source: WorkshopWorkflowSource,
        entry_node_ids: Sequence[str] | None = None,
    ) -> WorkshopWorkflowRecord:
        """创建工作流定义"""
        await self.require_project(project_id=project_id, user_id=user_id)
        key = model_key.strip()
        if not key:
            raise WorkshopRepositoryError("model_key required")
        row = await WorkshopWorkflows.create(
            id=workflow_id,
            project_id=project_id,
            name=name,
            steps=_dump_definition(nodes, edges, key, entry_node_ids),
            status=status.value,
            source=source.value,
            revision=1,
        )
        return _to_workflow(row)

    async def get_workflow(
        self,
        *,
        project_id: str,
        user_id: int,
        workflow_id: str,
    ) -> WorkshopWorkflowRecord:
        """读取工作流并校验归属"""
        await self.require_project(project_id=project_id, user_id=user_id)
        row = await WorkshopWorkflows.filter(
            id=workflow_id, project_id=project_id
        ).first()
        if row is None:
            raise WorkshopWorkflowNotFoundError(workflow_id)
        return _to_workflow(row)

    async def save_workflow_cas(
        self,
        *,
        project_id: str,
        user_id: int,
        workflow_id: str,
        expected_revision: int,
    ) -> WorkshopWorkflowRecord:
        """将草稿工作流 CAS 确认为已保存"""
        await self.require_project(project_id=project_id, user_id=user_id)
        updated = await WorkshopWorkflows.filter(
            id=workflow_id,
            project_id=project_id,
            revision=expected_revision,
            status=WorkshopWorkflowStatus.DRAFT.value,
        ).update(
            status=WorkshopWorkflowStatus.SAVED.value,
            revision=expected_revision + 1,
        )
        if updated != 1:
            raise WorkshopWorkflowConflictError(workflow_id)
        return await self.get_workflow(
            project_id=project_id, user_id=user_id, workflow_id=workflow_id
        )

    async def list_saved_workflows(
        self,
        *,
        project_id: str,
        user_id: int,
    ) -> list[WorkshopWorkflowRecord]:
        """列出已保存工作流"""
        await self.require_project(project_id=project_id, user_id=user_id)
        rows = await WorkshopWorkflows.filter(
            project_id=project_id,
            status=WorkshopWorkflowStatus.SAVED.value,
        ).order_by("updated_at", "id")
        return [_to_workflow(row) for row in rows]

    async def create_workflow_run(
        self,
        *,
        run_id: str,
        project_id: str,
        user_id: int,
        workflow_id: str,
        workflow_revision: int,
        trigger: WorkshopWorkflowRunTrigger,
        schedule_id: Optional[str] = None,
        status: WorkshopWorkflowRunStatus = WorkshopWorkflowRunStatus.QUEUED,
    ) -> WorkshopWorkflowRunRecord:
        """创建一次工作流运行记录"""
        await self.require_project(project_id=project_id, user_id=user_id)
        row = await WorkshopWorkflowRuns.create(
            id=run_id,
            project_id=project_id,
            workflow_id=workflow_id,
            workflow_revision=workflow_revision,
            schedule_id=schedule_id,
            trigger=trigger.value,
            status=status.value,
            current_node_id=None,
            error_message=None,
            started_at=None,
            finished_at=None,
            revision=1,
        )
        return _to_workflow_run(row)

    async def get_workflow_run(
        self,
        *,
        project_id: str,
        user_id: int,
        run_id: str,
    ) -> WorkshopWorkflowRunRecord:
        """读取运行记录并校验归属"""
        await self.require_project(project_id=project_id, user_id=user_id)
        row = await WorkshopWorkflowRuns.filter(
            id=run_id, project_id=project_id
        ).first()
        if row is None:
            raise WorkshopRepositoryError(f"unknown workflow run: {run_id}")
        return _to_workflow_run(row)

    async def list_workflow_runs(
        self,
        *,
        project_id: str,
        user_id: int,
        workflow_id: Optional[str] = None,
        limit: int = 50,
    ) -> list[WorkshopWorkflowRunRecord]:
        """列出项目运行记录"""
        await self.require_project(project_id=project_id, user_id=user_id)
        query = WorkshopWorkflowRuns.filter(project_id=project_id)
        if workflow_id is not None:
            query = query.filter(workflow_id=workflow_id)
        rows = await query.order_by("-created_at", "-id").limit(limit)
        return [_to_workflow_run(row) for row in rows]

    async def update_workflow_run_cas(
        self,
        *,
        project_id: str,
        user_id: int,
        run_id: str,
        expected_revision: int,
        status: WorkshopWorkflowRunStatus,
        current_node_id: Optional[str],
        error_message: Optional[str],
        started_at: Optional[datetime],
        finished_at: Optional[datetime],
    ) -> WorkshopWorkflowRunRecord:
        """CAS 更新运行记录状态"""
        await self.require_project(project_id=project_id, user_id=user_id)
        updated = await WorkshopWorkflowRuns.filter(
            id=run_id,
            project_id=project_id,
            revision=expected_revision,
        ).update(
            status=status.value,
            current_node_id=current_node_id,
            error_message=error_message,
            started_at=started_at,
            finished_at=finished_at,
            revision=expected_revision + 1,
        )
        if updated != 1:
            raise WorkshopRepositoryError(f"workflow run conflict: {run_id}")
        return await self.get_workflow_run(
            project_id=project_id, user_id=user_id, run_id=run_id
        )

    async def create_schedule(
        self,
        *,
        schedule_id: str,
        project_id: str,
        user_id: int,
        workflow_id: str,
        cron: str,
        timezone_name: str,
        authorized_at: datetime,
        authorized_external_capabilities: Iterable[WorkshopToolCapability],
        next_run_at: Optional[datetime] = None,
    ) -> WorkshopScheduleRecord:
        """创建定时定义并固化未来运行授权能力"""
        await self.require_project(project_id=project_id, user_id=user_id)
        workflow = await self.get_workflow(
            project_id=project_id, user_id=user_id, workflow_id=workflow_id
        )
        if workflow.status is not WorkshopWorkflowStatus.SAVED:
            raise WorkshopWorkflowNotFoundError(workflow_id)
        row = await WorkshopSchedules.create(
            id=schedule_id,
            project_id=project_id,
            workflow_id=workflow_id,
            cron=cron,
            timezone=timezone_name,
            enabled=True,
            authorized_at=authorized_at,
            authorized_external_capabilities=_dump_capabilities(
                authorized_external_capabilities
            ),
            next_run_at=next_run_at,
        )
        return _to_schedule(row)

    async def get_schedule(
        self,
        *,
        project_id: str,
        user_id: int,
        schedule_id: str,
    ) -> WorkshopScheduleRecord:
        """读取定时定义并校验归属"""
        await self.require_project(project_id=project_id, user_id=user_id)
        row = await WorkshopSchedules.filter(
            id=schedule_id, project_id=project_id
        ).first()
        if row is None:
            raise WorkshopScheduleNotFoundError(schedule_id)
        return _to_schedule(row)

    async def list_schedules(
        self,
        *,
        project_id: str,
        user_id: int,
    ) -> list[WorkshopScheduleRecord]:
        """按项目所有权列出定时定义"""
        await self.require_project(project_id=project_id, user_id=user_id)
        rows = await WorkshopSchedules.filter(project_id=project_id).order_by(
            "created_at", "id"
        )
        return [_to_schedule(row) for row in rows]

    async def disable_schedule(
        self,
        *,
        project_id: str,
        user_id: int,
        schedule_id: str,
    ) -> WorkshopScheduleRecord:
        """禁用定时：不可 claim，并清空 next_run_at"""
        await self.require_project(project_id=project_id, user_id=user_id)
        async with in_transaction() as connection:
            row = (
                await WorkshopSchedules.filter(id=schedule_id, project_id=project_id)
                .using_db(connection)
                .select_for_update()
                .first()
            )
            if row is None:
                raise WorkshopScheduleNotFoundError(schedule_id)
            await WorkshopSchedules.filter(id=schedule_id, project_id=project_id).using_db(
                connection
            ).update(enabled=False, next_run_at=None)
            refreshed = (
                await WorkshopSchedules.filter(id=schedule_id, project_id=project_id)
                .using_db(connection)
                .first()
            )
            if refreshed is None:
                raise WorkshopScheduleNotFoundError(schedule_id)
            return _to_schedule(refreshed)

    async def enable_schedule(
        self,
        *,
        project_id: str,
        user_id: int,
        schedule_id: str,
        next_run_at: datetime,
    ) -> WorkshopScheduleRecord:
        """启用定时：已启用时保留待执行时刻，否则在行锁下写入新时刻"""
        if next_run_at.tzinfo is None:
            raise WorkshopRepositoryError("next_run_at must be timezone-aware")
        await self.require_project(project_id=project_id, user_id=user_id)
        async with in_transaction() as connection:
            row = (
                await WorkshopSchedules.filter(id=schedule_id, project_id=project_id)
                .using_db(connection)
                .select_for_update()
                .first()
            )
            if row is None:
                raise WorkshopScheduleNotFoundError(schedule_id)
            if row.enabled and row.next_run_at is not None:
                return _to_schedule(row)
            await WorkshopSchedules.filter(id=schedule_id, project_id=project_id).using_db(
                connection
            ).update(enabled=True, next_run_at=next_run_at)
            refreshed = (
                await WorkshopSchedules.filter(id=schedule_id, project_id=project_id)
                .using_db(connection)
                .first()
            )
            if refreshed is None:
                raise WorkshopScheduleNotFoundError(schedule_id)
            return _to_schedule(refreshed)

    async def list_due_schedules(
        self,
        *,
        now: datetime,
        limit: int,
    ) -> list[WorkshopScheduleRecord]:
        """列出已到期且启用的定时候选项（按 next_run_at、id）"""
        if limit < 1:
            raise WorkshopRepositoryError("limit must be >= 1")
        if now.tzinfo is None:
            raise WorkshopRepositoryError("now must be timezone-aware")
        rows = (
            await WorkshopSchedules.filter(enabled=True, next_run_at__lte=now)
            .order_by("next_run_at", "id")
            .limit(limit)
        )
        return [_to_schedule(row) for row in rows]

    async def get_schedule_run_by_trigger_key(
        self,
        *,
        project_id: str,
        user_id: int,
        schedule_id: str,
        trigger_key: str,
    ) -> tuple[WorkshopTaskRecord, WorkshopEventRecord, WorkshopScheduleRunRecord]:
        """按 trigger_key 回读已持久化的运行、任务与开始事件"""
        await self.require_project(project_id=project_id, user_id=user_id)
        run_row = await WorkshopScheduleRuns.filter(
            project_id=project_id,
            schedule_id=schedule_id,
            trigger_key=trigger_key,
        ).first()
        if run_row is None:
            raise WorkshopScheduleNotFoundError(schedule_id)
        return await self._load_trigger_bundle(
            project_id=project_id,
            run_row=run_row,
            connection=None,
        )

    async def create_executing_task_from_workflow(
        self,
        *,
        project_id: str,
        user_id: int,
        workflow_id: str,
        task_id: str,
        external_auth: Iterable[WorkshopToolCapability] = (),
    ) -> WorkshopTaskRecord:
        """从已保存工作流一次性创建 executing 任务并固化外部授权"""
        await self.require_project(project_id=project_id, user_id=user_id)
        grants = frozenset(external_auth)
        async with in_transaction() as connection:
            workflow_row = await self._load_workflow_row(
                project_id=project_id,
                workflow_id=workflow_id,
                connection=connection,
                require_saved=True,
            )
            workflow = _to_workflow(workflow_row)
            task = await WorkshopTasks.create(
                id=task_id,
                project_id=project_id,
                title=workflow.name,
                goals=list(_goals_from_nodes(workflow.nodes)),
                required_artifacts=list(
                    _aggregate_required_artifacts(workflow.nodes)
                ),
                status=WorkshopTaskStatus.EXECUTING.value,
                schedule_id=None,
                schedule_authorized=False,
                external_auth=_dump_capabilities(grants),
                revision=1,
                using_db=connection,
            )
        return _to_task(task)

    async def trigger_schedule_run(
        self,
        *,
        project_id: str,
        user_id: int,
        schedule_id: str,
        trigger_key: str,
        task_id: str,
        started_event_key: str,
    ) -> tuple[WorkshopTaskRecord, WorkshopEventRecord, WorkshopScheduleRunRecord]:
        """定时触发：同 trigger_key 幂等返回已有运行，否则原子创建"""
        await self.require_project(project_id=project_id, user_id=user_id)
        try:
            async with in_transaction() as connection:
                existing = (
                    await WorkshopScheduleRuns.filter(
                        project_id=project_id,
                        schedule_id=schedule_id,
                        trigger_key=trigger_key,
                    )
                    .using_db(connection)
                    .first()
                )
                if existing is not None:
                    return await self._load_trigger_bundle(
                        project_id=project_id,
                        run_row=existing,
                        connection=connection,
                    )
                schedule_row = (
                    await WorkshopSchedules.filter(
                        id=schedule_id, project_id=project_id
                    )
                    .using_db(connection)
                    .first()
                )
                if schedule_row is None:
                    raise WorkshopScheduleNotFoundError(schedule_id)
                if not schedule_row.enabled:
                    raise WorkshopRepositoryError("schedule is disabled")
                return await self._insert_schedule_trigger(
                    connection=connection,
                    schedule_row=schedule_row,
                    trigger_key=trigger_key,
                    task_id=task_id,
                    started_event_key=started_event_key,
                )
        except WorkshopScheduleTriggerConflictError:
            return await self.get_schedule_run_by_trigger_key(
                project_id=project_id,
                user_id=user_id,
                schedule_id=schedule_id,
                trigger_key=trigger_key,
            )

    async def claim_due_schedule_run(
        self,
        *,
        schedule_id: str,
        expected_next_run_at: datetime,
        task_id: str,
        started_event_key: str,
    ) -> (
        tuple[WorkshopTaskRecord, WorkshopEventRecord, WorkshopScheduleRunRecord]
        | None
    ):
        """多 worker 到期 claim：SKIP LOCKED + next_run_at CAS，失败返回 None"""
        if expected_next_run_at.tzinfo is None:
            raise WorkshopRepositoryError("expected_next_run_at must be timezone-aware")
        try:
            async with in_transaction() as connection:
                schedule_row = (
                    await WorkshopSchedules.filter(
                        id=schedule_id,
                        enabled=True,
                        next_run_at=expected_next_run_at,
                    )
                    .using_db(connection)
                    .select_for_update(skip_locked=True)
                    .first()
                )
                if schedule_row is None:
                    return None
                due_fire_at = schedule_row.next_run_at
                if due_fire_at is None:
                    return None
                if due_fire_at.tzinfo is None:
                    due_fire_at = due_fire_at.replace(tzinfo=timezone.utc)
                trigger_key = schedule_trigger_key(
                    schedule_id=schedule_id, due_fire_at=due_fire_at
                )
                existing = (
                    await WorkshopScheduleRuns.filter(
                        schedule_id=schedule_id,
                        trigger_key=trigger_key,
                    )
                    .using_db(connection)
                    .first()
                )
                if existing is not None:
                    advanced = compute_next_run_at(
                        cron=schedule_row.cron,
                        timezone_name=schedule_row.timezone,
                        after=due_fire_at,
                        previous_fire_time=due_fire_at,
                    )
                    updated = (
                        await WorkshopSchedules.filter(
                            id=schedule_id,
                            next_run_at=expected_next_run_at,
                        )
                        .using_db(connection)
                        .update(next_run_at=advanced)
                    )
                    if updated != 1:
                        raise WorkshopScheduleClaimLostError(schedule_id)
                    return await self._load_trigger_bundle(
                        project_id=schedule_row.project_id,
                        run_row=existing,
                        connection=connection,
                    )
                advanced = compute_next_run_at(
                    cron=schedule_row.cron,
                    timezone_name=schedule_row.timezone,
                    after=due_fire_at,
                    previous_fire_time=due_fire_at,
                )
                updated = (
                    await WorkshopSchedules.filter(
                        id=schedule_id,
                        next_run_at=expected_next_run_at,
                    )
                    .using_db(connection)
                    .update(next_run_at=advanced)
                )
                if updated != 1:
                    raise WorkshopScheduleClaimLostError(schedule_id)
                return await self._insert_schedule_trigger(
                    connection=connection,
                    schedule_row=schedule_row,
                    trigger_key=trigger_key,
                    task_id=task_id,
                    started_event_key=started_event_key,
                )
        except WorkshopScheduleTriggerConflictError:
            return None
        except WorkshopScheduleClaimLostError:
            return None

    async def _insert_schedule_trigger(
        self,
        *,
        connection: BaseDBAsyncClient,
        schedule_row: WorkshopSchedules,
        trigger_key: str,
        task_id: str,
        started_event_key: str,
    ) -> tuple[WorkshopTaskRecord, WorkshopEventRecord, WorkshopScheduleRunRecord]:
        """在事务内写入 executing 任务、运行记录与开始事件"""
        schedule_id = schedule_row.id
        project_id = schedule_row.project_id
        schedule = _to_schedule(schedule_row)
        workflow_row = await self._load_workflow_row(
            project_id=project_id,
            workflow_id=schedule.workflow_id,
            connection=connection,
            require_saved=True,
        )
        workflow = _to_workflow(workflow_row)
        task = await WorkshopTasks.create(
            id=task_id,
            project_id=project_id,
            title=workflow.name,
            goals=list(_goals_from_nodes(workflow.nodes)),
            required_artifacts=list(_aggregate_required_artifacts(workflow.nodes)),
            status=WorkshopTaskStatus.EXECUTING.value,
            schedule_id=schedule_id,
            schedule_authorized=True,
            external_auth=_dump_capabilities(
                schedule.authorized_external_capabilities
            ),
            revision=1,
            using_db=connection,
        )
        try:
            run_row = await WorkshopScheduleRuns.create(
                id=new_id("srun"),
                project_id=project_id,
                schedule_id=schedule_id,
                trigger_key=trigger_key,
                task_id=task_id,
                using_db=connection,
            )
        except IntegrityError as exc:
            if _is_unique_violation(exc):
                raise WorkshopScheduleTriggerConflictError(trigger_key) from exc
            raise
        started_payload = ScheduleStartedPayload(
            schedule_id=schedule_id,
            task_id=task_id,
            trigger_key=trigger_key,
            message=f"定时任务 {schedule_id} 开始",
        )
        event_row = await WorkshopEvents.create(
            event_key=started_event_key,
            project_id=project_id,
            task_id=task_id,
            kind=WorkshopEventKind.SCHEDULE_STARTED.value,
            payload=_dump_event_payload(
                WorkshopEventKind.SCHEDULE_STARTED, started_payload
            ),
            using_db=connection,
        )
        return _to_task(task), _to_event(event_row), _to_schedule_run(run_row)

    async def _load_trigger_bundle(
        self,
        *,
        project_id: str,
        run_row: WorkshopScheduleRuns,
        connection: BaseDBAsyncClient | None,
    ) -> tuple[WorkshopTaskRecord, WorkshopEventRecord, WorkshopScheduleRunRecord]:
        """按已有运行记录装载任务与 SCHEDULE_STARTED 事件"""
        task_query = WorkshopTasks.filter(id=run_row.task_id, project_id=project_id)
        event_query = WorkshopEvents.filter(
            project_id=project_id,
            task_id=run_row.task_id,
            kind=WorkshopEventKind.SCHEDULE_STARTED.value,
        ).order_by("created_at", "id")
        if connection is not None:
            task_query = task_query.using_db(connection)
            event_query = event_query.using_db(connection)
        task_row = await task_query.first()
        if task_row is None:
            raise WorkshopTaskNotFoundError(run_row.task_id)
        event_row = await event_query.first()
        if event_row is None:
            raise WorkshopRepositoryError(
                f"missing schedule_started event for task {run_row.task_id}"
            )
        return _to_task(task_row), _to_event(event_row), _to_schedule_run(run_row)

    async def complete_weak_accept(
        self,
        *,
        project_id: str,
        user_id: int,
        task_id: str,
        expected_revision: int,
        artifacts: Sequence[ArtifactSubmission],
    ) -> WorkshopTaskRecord:
        """弱验收成功：CAS 完成并写入全部产物"""
        await self.require_project(project_id=project_id, user_id=user_id)
        async with in_transaction() as connection:
            task = await self.update_task_status_cas(
                project_id=project_id,
                user_id=user_id,
                task_id=task_id,
                expected_revision=expected_revision,
                expected_status=WorkshopTaskStatus.REVIEWING,
                target_status=WorkshopTaskStatus.DONE,
                connection=connection,
            )
            await self._insert_artifacts(
                project_id=project_id,
                task_id=task_id,
                artifacts=artifacts,
                connection=connection,
            )
        return task

    async def complete_scheduled_success(
        self,
        *,
        project_id: str,
        user_id: int,
        task_id: str,
        expected_revision: int,
        artifacts: Sequence[ArtifactSubmission],
        summary_event_key: str,
        published_event_key: str,
    ) -> tuple[WorkshopTaskRecord, WorkshopEventRecord, WorkshopEventRecord]:
        """定时弱验收成功：完成、产物与摘要/发布事件"""
        await self.require_project(project_id=project_id, user_id=user_id)
        async with in_transaction() as connection:
            task = await self.update_task_status_cas(
                project_id=project_id,
                user_id=user_id,
                task_id=task_id,
                expected_revision=expected_revision,
                expected_status=WorkshopTaskStatus.REVIEWING,
                target_status=WorkshopTaskStatus.DONE,
                connection=connection,
            )
            await self._insert_artifacts(
                project_id=project_id,
                task_id=task_id,
                artifacts=artifacts,
                connection=connection,
            )
            summary_payload = ScheduleSummaryPayload(
                task_id=task_id,
                message=f"任务 {task_id} 完成摘要",
            )
            summary_row = await WorkshopEvents.create(
                event_key=summary_event_key,
                project_id=project_id,
                task_id=task_id,
                kind=WorkshopEventKind.SCHEDULE_SUMMARY.value,
                payload=_dump_event_payload(
                    WorkshopEventKind.SCHEDULE_SUMMARY, summary_payload
                ),
                using_db=connection,
            )
            published_payload = ArtifactsPublishedPayload(
                task_id=task_id,
                artifact_names=tuple(artifact.name.strip() for artifact in artifacts),
            )
            published_row = await WorkshopEvents.create(
                event_key=published_event_key,
                project_id=project_id,
                task_id=task_id,
                kind=WorkshopEventKind.ARTIFACTS_PUBLISHED.value,
                payload=_dump_event_payload(
                    WorkshopEventKind.ARTIFACTS_PUBLISHED, published_payload
                ),
                using_db=connection,
            )
        return task, _to_event(summary_row), _to_event(published_row)

    async def complete_scheduled_blocked(
        self,
        *,
        project_id: str,
        user_id: int,
        task_id: str,
        expected_revision: int,
        reasons: Sequence[str],
        blocked_event_key: str,
    ) -> tuple[WorkshopTaskRecord, WorkshopEventRecord]:
        """定时弱验收失败：blocked 与阻塞事件"""
        await self.require_project(project_id=project_id, user_id=user_id)
        async with in_transaction() as connection:
            task = await self.update_task_status_cas(
                project_id=project_id,
                user_id=user_id,
                task_id=task_id,
                expected_revision=expected_revision,
                expected_status=WorkshopTaskStatus.REVIEWING,
                target_status=WorkshopTaskStatus.BLOCKED,
                connection=connection,
            )
            blocked_payload = ScheduleBlockedPayload(
                task_id=task_id,
                message=f"任务 {task_id} 待处理",
                reasons=tuple(reasons),
            )
            event_row = await WorkshopEvents.create(
                event_key=blocked_event_key,
                project_id=project_id,
                task_id=task_id,
                kind=WorkshopEventKind.SCHEDULE_BLOCKED.value,
                payload=_dump_event_payload(
                    WorkshopEventKind.SCHEDULE_BLOCKED, blocked_payload
                ),
                using_db=connection,
            )
        return task, _to_event(event_row)

    async def create_artifact(
        self,
        *,
        artifact_id: str,
        project_id: str,
        user_id: int,
        task_id: Optional[str],
        artifact: ArtifactSubmission,
    ) -> str:
        """持久化产物索引"""
        await self.require_project(project_id=project_id, user_id=user_id)
        if task_id is not None:
            await self._assert_task_in_project(
                project_id=project_id, task_id=task_id
            )
        storage_key, size_bytes, metadata = _artifact_row_fields(
            artifact, artifact_id=artifact_id
        )
        try:
            await WorkshopArtifacts.create(
                id=artifact_id,
                project_id=project_id,
                task_id=task_id,
                name=artifact.name.strip(),
                storage_type=artifact.storage_type.value,
                storage_key=storage_key,
                size_bytes=size_bytes,
                metadata=metadata,
            )
        except IntegrityError as exc:
            raise WorkshopArtifactConflictError(artifact.name.strip()) from exc
        return artifact_id

    async def append_event(
        self,
        *,
        event_key: str,
        project_id: str,
        user_id: int,
        kind: WorkshopEventKind,
        payload: WorkshopEventPayload,
        task_id: Optional[str] = None,
    ) -> WorkshopEventRecord:
        """追加一条工坊系统事件"""
        await self.require_project(project_id=project_id, user_id=user_id)
        if task_id is not None:
            await self._assert_task_in_project(
                project_id=project_id, task_id=task_id
            )
        row = await WorkshopEvents.create(
            event_key=event_key,
            project_id=project_id,
            task_id=task_id,
            kind=kind.value,
            payload=_dump_event_payload(kind, payload),
        )
        return _to_event(row)

    async def list_events(
        self,
        *,
        project_id: str,
        user_id: int,
    ) -> list[WorkshopEventRecord]:
        """按创建顺序列出项目事件"""
        await self.require_project(project_id=project_id, user_id=user_id)
        rows = await WorkshopEvents.filter(project_id=project_id).order_by(
            "created_at", "id"
        )
        return [_to_event(row) for row in rows]

    async def record_capability_use(
        self,
        *,
        project_id: str,
        user_id: int,
        task_id: str,
        capability: WorkshopToolCapability,
    ) -> None:
        """幂等记录任务实际使用过的工具能力；并发唯一冲突视为成功"""
        await self.require_task(
            project_id=project_id, user_id=user_id, task_id=task_id
        )
        try:
            await WorkshopTaskCapabilityUses.create(
                project_id=project_id,
                task_id=task_id,
                capability=capability.value,
            )
        except IntegrityError as exc:
            if _constraint_name(exc) == UK_WORKSHOP_TASK_CAPABILITY_USES_TASK_CAP:
                return
            raise

    async def list_capability_uses(
        self,
        *,
        project_id: str,
        user_id: int,
        task_id: str,
    ) -> frozenset[WorkshopToolCapability]:
        """读取任务已记录的能力使用集合"""
        await self.require_task(
            project_id=project_id, user_id=user_id, task_id=task_id
        )
        rows = await WorkshopTaskCapabilityUses.filter(
            project_id=project_id, task_id=task_id
        ).all()
        return frozenset(
            WorkshopToolCapability(row.capability) for row in rows
        )


def new_id(prefix: str) -> str:
    """生成带前缀的字符串主键"""
    return f"{prefix}_{uuid4().hex}"


def _preset_expert_id(project_id: str, preset_key: str) -> str:
    """预置专家 id；长 preset_key 时用稳定 hash 后缀以满足 64 字符上限"""
    candidate = f"{project_id}_{preset_key}"
    if len(candidate) <= 64:
        return candidate
    import hashlib

    suffix = hashlib.sha256(preset_key.encode("utf-8")).hexdigest()[:16]
    prefix = project_id[: 64 - 1 - len(suffix)]
    return f"{prefix}_{suffix}"


def utc_now() -> datetime:
    """返回当前 UTC 时间"""
    return datetime.now(timezone.utc)
