from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Tuple
from app.contracts.ecommerce import ShopConnectionPublic
from app.server.infra.config import settings
from app.server.workshop.domain.ecommerce.oauth import (
    InMemoryCredentialStore,
    OAuthClientConfig,
    TaobaoAppType,
    TaobaoOAuthService,
)
from app.server.workshop.domain.enums import (
    WorkshopExpertKind,
    WorkshopRole,
    WorkshopTaskStatus,
)
from app.server.workshop.domain.presets import get_preset, presets_for_keys
from app.server.workshop.domain.types import (
    CreateTaskProposal,
    ImportErrorRecord,
    RosterExpert,
    WorkshopProjectRecord,
)
from app.server.workshop.persistence.repository import (
    WorkshopExpertNotFoundError,
    WorkshopGroupChatOwnershipError,
    WorkshopProjectConflictError,
    WorkshopProjectNotFoundError,
    WorkshopProposalNotFoundError,
    WorkshopRepository,
    new_id,
)


class WorkshopProjectError(Exception):
    """非法工坊项目操作（fail closed）"""


_taobao_oauth_service: TaobaoOAuthService | None = None
_taobao_oauth_http: object | None = None


def _resolve_taobao_oauth_service() -> TaobaoOAuthService | None:
    """有完整淘宝应用配置时返回 OAuth 服务；否则 None"""
    global _taobao_oauth_service, _taobao_oauth_http
    client_id = (settings.TAOBAO_OAUTH_CLIENT_ID or "").strip()
    client_secret = (settings.TAOBAO_OAUTH_CLIENT_SECRET or "").strip()
    redirect_uri = (settings.TAOBAO_OAUTH_REDIRECT_URI or "").strip()
    if not client_id or not client_secret or not redirect_uri:
        return None
    if _taobao_oauth_service is not None:
        return _taobao_oauth_service
    import httpx

    _taobao_oauth_http = httpx.Client(timeout=30.0)
    _taobao_oauth_service = TaobaoOAuthService(
        config=OAuthClientConfig(
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=redirect_uri,
            app_type=TaobaoAppType.SELF_DEV,
        ),
        store=InMemoryCredentialStore(),
        http_client=_taobao_oauth_http,  # type: ignore[arg-type]
    )
    return _taobao_oauth_service


@dataclass(frozen=True, slots=True)
class WorkshopProject:
    """工坊项目（与创作域 Project 隔离）"""

    id: str
    user_id: int
    name: str
    group_chat_id: int
    host_role: WorkshopRole = WorkshopRole.HOST


@dataclass(frozen=True, slots=True)
class UpgradeResult:
    """单 Agent 升级为工坊项目的结果"""

    project: WorkshopProject
    group_chat_id: int
    carried_message_count: int
    pending_task_proposal: CreateTaskProposal | None = None


@dataclass(frozen=True, slots=True)
class WakeDashboard:
    """唤醒仪表盘投影"""

    group_chat_id: int
    pinned_task_ids: Tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DataSourceStatusRecord:
    """电商数据源状态读模型"""

    key: str
    label: str
    status: str
    detail: str | None = None
    last_synced_at: str | None = None


@dataclass(frozen=True, slots=True)
class DataSourcesRecord:
    """电商数据源聚合读模型"""

    shop: ShopConnectionPublic | None
    sources: Tuple[DataSourceStatusRecord, ...]
    quality_issues: Tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ImportErrorsRecord:
    """电商导入错误列表读模型"""

    items: Tuple[ImportErrorRecord, ...]
    unsupported_template: bool


@dataclass(frozen=True, slots=True)
class BeginShopAuthRecord:
    """发起店铺授权读模型"""

    authorize_url: str | None
    shop_connection_id: str
    status: str = "authorizing"


_DEFAULT_ECOM_SOURCE_SPECS: tuple[tuple[str, str], ...] = ()


_SHOP_CONNECTION_PUBLIC_STATUSES = frozenset(
    {"connected", "REAUTH_REQUIRED", "disconnected", "unsupported"}
)


def _parse_shop_connection_public(raw: object) -> ShopConnectionPublic | None:
    """从 brief shop_connection 解析对外视图；非法或 authorizing 返回 None"""
    if not isinstance(raw, dict):
        return None
    status = raw.get("status")
    if not isinstance(status, str) or status not in _SHOP_CONNECTION_PUBLIC_STATUSES:
        return None
    try:
        return ShopConnectionPublic.model_validate(raw)
    except ValueError:
        return None


def _default_ecom_sources() -> tuple[DataSourceStatusRecord, ...]:
    """电商包默认数据源均为 disconnected"""
    return tuple(
        DataSourceStatusRecord(key=key, label=label, status="disconnected")
        for key, label in _DEFAULT_ECOM_SOURCE_SPECS
    )


def _parse_data_source_status(raw: object) -> DataSourceStatusRecord | None:
    """从 brief data_sources 项解析状态"""
    if not isinstance(raw, dict):
        return None
    key = raw.get("key")
    label = raw.get("label")
    status = raw.get("status")
    if not isinstance(key, str) or not key.strip():
        return None
    if not isinstance(label, str) or not label.strip():
        return None
    if not isinstance(status, str) or not status.strip():
        return None
    detail = raw.get("detail")
    last_synced_at = raw.get("last_synced_at")
    return DataSourceStatusRecord(
        key=key.strip(),
        label=label.strip(),
        status=status.strip(),
        detail=detail if isinstance(detail, str) else None,
        last_synced_at=last_synced_at if isinstance(last_synced_at, str) else None,
    )


def _parse_data_sources(brief: dict[str, object]) -> tuple[DataSourceStatusRecord, ...]:
    """解析 brief data_sources；缺省回退电商 disconnected 默认项"""
    raw = brief.get("data_sources")
    if raw is None:
        return _default_ecom_sources()
    if not isinstance(raw, list):
        raise WorkshopProjectError("brief data_sources must be list")
    parsed = [_parse_data_source_status(item) for item in raw]
    if any(item is None for item in parsed):
        raise WorkshopProjectError("brief data_sources item invalid")
    return tuple(item for item in parsed if item is not None)


def _parse_quality_issues(brief: dict[str, object]) -> tuple[str, ...]:
    """解析 brief quality_issues"""
    raw = brief.get("quality_issues")
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise WorkshopProjectError("brief quality_issues must be list")
    return tuple(str(item) for item in raw)


def _parse_import_errors(brief: dict[str, object]) -> ImportErrorsRecord:
    """解析 brief import_errors"""
    raw = brief.get("import_errors")
    unsupported_template = bool(brief.get("unsupported_template", False))
    if raw is None:
        return ImportErrorsRecord(items=(), unsupported_template=unsupported_template)
    if not isinstance(raw, list):
        raise WorkshopProjectError("brief import_errors must be list")
    items: list[ImportErrorRecord] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise WorkshopProjectError("brief import_errors item must be object")
        row_index = item.get("row_index", index)
        code = item.get("code")
        message = item.get("message")
        if not isinstance(row_index, int):
            raise WorkshopProjectError("brief import_errors row_index must be int")
        if not isinstance(code, str) or not code.strip():
            raise WorkshopProjectError("brief import_errors code required")
        if not isinstance(message, str) or not message.strip():
            raise WorkshopProjectError("brief import_errors message required")
        items.append(
            ImportErrorRecord(
                row_index=row_index,
                code=code.strip(),
                message=message.strip(),
            )
        )
    return ImportErrorsRecord(items=tuple(items), unsupported_template=unsupported_template)


def _to_project(record: WorkshopProjectRecord) -> WorkshopProject:
    """仓储读模型转应用 DTO；host_role 为 HOST 不变量"""
    if record.host_role is not WorkshopRole.HOST:
        raise WorkshopProjectError("workshop project host_role must be HOST")
    return WorkshopProject(
        id=record.id,
        user_id=record.user_id,
        name=record.name,
        group_chat_id=record.group_chat_id,
        host_role=WorkshopRole.HOST,
    )


class WorkshopProjectService:
    """工坊项目与名册应用服务"""

    def __init__(self, repository: WorkshopRepository) -> None:
        """注入工坊仓储"""
        self._repository = repository

    async def create_project(
        self,
        *,
        user_id: int,
        name: str,
        group_chat_id: int,
        initial_expert_keys: Tuple[str, ...] = (),
    ) -> WorkshopProject:
        """显式创建工坊项目并初始化预置名册"""
        if not name.strip():
            raise WorkshopProjectError("project name required")
        keys = tuple(initial_expert_keys)
        presets = presets_for_keys(keys) if keys else ()
        try:
            record = await self._repository.create_project_with_presets(
                project_id=new_id("wp"),
                user_id=user_id,
                name=name.strip(),
                group_chat_id=group_chat_id,
                presets=presets,
                bootstrap_keys=list(keys),
            )
        except WorkshopProjectConflictError as exc:
            raise WorkshopProjectError(str(exc)) from exc
        except WorkshopGroupChatOwnershipError as exc:
            raise WorkshopProjectError(str(exc)) from exc
        return _to_project(record)

    async def get_project(self, *, project_id: str, user_id: int) -> WorkshopProject:
        """读取工坊项目"""
        record = await self._repository.get_project(
            project_id=project_id, user_id=user_id
        )
        if record is None:
            raise WorkshopProjectError(f"unknown project: {project_id}")
        return _to_project(record)

    async def get_project_by_group_chat(
        self, *, user_id: int, group_chat_id: int
    ) -> WorkshopProject | None:
        """按群聊 id 读取工坊项目；非工坊群聊返回 None"""
        record = await self._repository.get_project_by_group_chat(
            user_id=user_id, group_chat_id=group_chat_id
        )
        if record is None:
            return None
        return _to_project(record)

    async def list_projects(self, *, user_id: int) -> Tuple[WorkshopProject, ...]:
        """列出当前用户的工坊项目"""
        records = await self._repository.list_projects_for_user(user_id=user_id)
        return tuple(_to_project(record) for record in records)

    async def get_data_sources(
        self, *, project_id: str, user_id: int
    ) -> DataSourcesRecord:
        """读取电商数据源状态；从 brief 解析，禁止编造 connected"""
        await self.get_project(project_id=project_id, user_id=user_id)
        brief = await self._repository.get_project_brief(
            project_id=project_id, user_id=user_id
        )
        shop = _parse_shop_connection_public(brief.get("shop_connection"))
        quality_issues = _parse_quality_issues(brief)
        return DataSourcesRecord(
            shop=shop,
            sources=_parse_data_sources(brief),
            quality_issues=quality_issues,
        )

    async def list_import_errors(
        self, *, project_id: str, user_id: int
    ) -> ImportErrorsRecord:
        """读取 brief 中的导入错误列表"""
        await self.get_project(project_id=project_id, user_id=user_id)
        brief = await self._repository.get_project_brief(
            project_id=project_id, user_id=user_id
        )
        return _parse_import_errors(brief)

    async def begin_shop_auth(
        self, *, project_id: str, user_id: int
    ) -> BeginShopAuthRecord:
        """发起店铺 OAuth；未配置淘宝应用时 fail closed，不写 authorizing"""
        await self.get_project(project_id=project_id, user_id=user_id)
        oauth = _resolve_taobao_oauth_service()
        if oauth is None:
            raise WorkshopProjectError("当前环境未配置淘宝应用")
        brief = await self._repository.get_project_brief(
            project_id=project_id, user_id=user_id
        )
        shop_raw = brief.get("shop_connection")
        shop_connection: dict[str, Any]
        if isinstance(shop_raw, dict):
            shop_connection = dict(shop_raw)
        else:
            shop_connection = {}
        existing_id = shop_connection.get("shop_connection_id")
        shop_connection_id = (
            existing_id.strip()
            if isinstance(existing_id, str) and existing_id.strip()
            else new_id("shop_conn")
        )
        authorize_url = oauth.build_authorize_url(shop_connection_id=shop_connection_id)
        shop_connection["shop_connection_id"] = shop_connection_id
        shop_connection["status"] = "authorizing"
        brief["shop_connection"] = shop_connection
        await self._repository.update_project_brief(
            project_id=project_id, user_id=user_id, brief=brief
        )
        return BeginShopAuthRecord(
            authorize_url=authorize_url,
            shop_connection_id=shop_connection_id,
            status="authorizing",
        )

    async def attach_second_group_chat(
        self, *, project_id: str, user_id: int
    ) -> None:
        """禁止为同一工坊项目挂第二条群聊"""
        await self.get_project(project_id=project_id, user_id=user_id)
        raise WorkshopProjectError("workshop project allows exactly one group chat")

    async def list_roster(
        self, *, project_id: str, user_id: int
    ) -> Tuple[RosterExpert, ...]:
        """列出项目名册"""
        experts = await self._repository.list_roster(
            project_id=project_id, user_id=user_id
        )
        return tuple(experts)

    async def mutate_preset_in_place(
        self,
        *,
        project_id: str,
        user_id: int,
        preset_key: str,
        name: str,
    ) -> None:
        """预置专家禁止原地修改"""
        await self.get_project(project_id=project_id, user_id=user_id)
        get_preset(preset_key)
        raise WorkshopProjectError("preset experts are read-only; copy to customize")

    async def copy_preset_to_custom(
        self,
        *,
        project_id: str,
        user_id: int,
        preset_key: str,
        name: str,
    ) -> RosterExpert:
        """复制预置为项目定制专家"""
        if not name.strip():
            raise WorkshopProjectError("expert name required")
        preset = get_preset(preset_key)
        return await self._repository.add_custom_expert(
            project_id=project_id,
            user_id=user_id,
            expert_id=new_id("re_custom"),
            name=name.strip(),
            kind=preset.kind,
            source_preset_key=preset.key,
        )

    async def add_preset_to_roster(
        self,
        *,
        project_id: str,
        user_id: int,
        preset_key: str,
    ) -> RosterExpert:
        """将平台预置专家加入名册；已存在则直接返回"""
        preset = get_preset(preset_key)
        return await self._repository.add_preset_expert(
            project_id=project_id,
            user_id=user_id,
            preset=preset,
        )

    async def host_propose_custom_expert(
        self,
        *,
        project_id: str,
        user_id: int,
        name: str,
        kind: WorkshopExpertKind,
    ) -> str:
        """主持提议定制专家，返回提议 id，待用户确认入库"""
        if not name.strip():
            raise WorkshopProjectError("expert name required")
        return await self._repository.create_expert_proposal(
            proposal_id=new_id("exp_prop"),
            project_id=project_id,
            user_id=user_id,
            name=name.strip(),
            kind=kind,
        )

    async def user_confirm_custom_expert(
        self,
        *,
        project_id: str,
        user_id: int,
        proposal_id: str,
    ) -> RosterExpert:
        """用户确认定制专家并写入项目名册"""
        try:
            return await self._repository.confirm_expert_proposal(
                project_id=project_id,
                user_id=user_id,
                proposal_id=proposal_id,
                expert_id=new_id("re_custom"),
            )
        except WorkshopProjectNotFoundError as exc:
            raise WorkshopProjectError(f"unknown project: {project_id}") from exc
        except WorkshopProposalNotFoundError as exc:
            raise WorkshopProjectError(
                f"unknown expert proposal: {proposal_id}"
            ) from exc

    async def user_decline_custom_expert(
        self,
        *,
        project_id: str,
        user_id: int,
        proposal_id: str,
    ) -> None:
        """用户拒绝定制专家提议"""
        try:
            await self._repository.decline_expert_proposal(
                project_id=project_id,
                user_id=user_id,
                proposal_id=proposal_id,
            )
        except WorkshopProjectNotFoundError as exc:
            raise WorkshopProjectError(f"unknown project: {project_id}") from exc
        except WorkshopProposalNotFoundError as exc:
            raise WorkshopProjectError(
                f"unknown expert proposal: {proposal_id}"
            ) from exc

    async def invite_to_room(
        self, *, project_id: str, user_id: int, expert_id: str
    ) -> None:
        """邀请名册专家进入当前群聊房间"""
        try:
            await self._repository.add_room_member(
                project_id=project_id, user_id=user_id, expert_id=expert_id
            )
        except WorkshopExpertNotFoundError as exc:
            raise WorkshopProjectError(f"expert not on roster: {expert_id}") from exc

    async def room_members(self, *, project_id: str, user_id: int) -> set[str]:
        """当前房间在场专家"""
        return await self._repository.list_room_members(
            project_id=project_id, user_id=user_id
        )

    async def confirm_upgrade_to_project(
        self,
        *,
        user_id: int,
        group_chat_id: int,
        project_name: str,
        seed_goal: str = "",
        carried_message_count: int = 0,
        initial_expert_keys: Tuple[str, ...] = (),
    ) -> UpgradeResult:
        """单 Agent 升级：创建项目与预置名册，不自动推送任务提议"""
        del seed_goal  # 兼容旧入参；升级不再据此立任务
        if not project_name.strip():
            raise WorkshopProjectError("project name required")
        if carried_message_count < 0:
            raise WorkshopProjectError("carried_message_count must be >= 0")
        keys = tuple(initial_expert_keys)
        presets = presets_for_keys(keys) if keys else ()
        try:
            project_record = await self._repository.create_project_with_presets(
                project_id=new_id("wp"),
                user_id=user_id,
                name=project_name.strip(),
                group_chat_id=group_chat_id,
                presets=presets,
                bootstrap_keys=list(keys),
            )
        except WorkshopProjectConflictError as exc:
            raise WorkshopProjectError(str(exc)) from exc
        except WorkshopGroupChatOwnershipError as exc:
            raise WorkshopProjectError(str(exc)) from exc
        return UpgradeResult(
            project=_to_project(project_record),
            group_chat_id=group_chat_id,
            carried_message_count=carried_message_count,
            pending_task_proposal=None,
        )

    async def confirm_upgrade_from_expert(
        self,
        *,
        user_id: int,
        group_chat_id: int,
        project_name: str,
        seed_goal: str = "",
        carried_message_count: int = 0,
        expert_key: str,
    ) -> UpgradeResult:
        """单专家升级：名册只含该专家，并立即进入房间；不自动推送任务提议"""
        del seed_goal  # 兼容旧入参；升级不再据此立任务
        return await self.confirm_upgrade_with_experts(
            user_id=user_id,
            group_chat_id=group_chat_id,
            project_name=project_name,
            carried_message_count=carried_message_count,
            expert_keys=(expert_key,),
        )

    async def confirm_upgrade_with_experts(
        self,
        *,
        user_id: int,
        group_chat_id: int,
        project_name: str,
        carried_message_count: int = 0,
        expert_keys: Tuple[str, ...],
    ) -> UpgradeResult:
        """多专家升级：种子名册并全部进房；不自动推送任务提议"""
        if not project_name.strip():
            raise WorkshopProjectError("project name required")
        if carried_message_count < 0:
            raise WorkshopProjectError("carried_message_count must be >= 0")
        if not expert_keys:
            raise WorkshopProjectError("at least one expert_key required")
        try:
            presets = presets_for_keys(expert_keys)
        except KeyError as exc:
            raise WorkshopProjectError(f"unknown expert preset: {exc.args[0]}") from exc
        try:
            project_record = await self._repository.create_project_with_presets(
                project_id=new_id("wp"),
                user_id=user_id,
                name=project_name.strip(),
                group_chat_id=group_chat_id,
                presets=presets,
                bootstrap_keys=list(expert_keys),
            )
        except WorkshopProjectConflictError as exc:
            raise WorkshopProjectError(str(exc)) from exc
        except WorkshopGroupChatOwnershipError as exc:
            raise WorkshopProjectError(str(exc)) from exc
        roster = await self._repository.list_roster(
            project_id=project_record.id, user_id=user_id
        )
        by_preset = {
            expert.preset_key: expert.id
            for expert in roster
            if expert.preset_key
        }
        for key in expert_keys:
            expert_id = by_preset.get(key)
            if expert_id is None:
                raise WorkshopProjectError(f"expert not seeded: {key}")
            await self._repository.add_room_member(
                project_id=project_record.id, user_id=user_id, expert_id=expert_id
            )
        return UpgradeResult(
            project=_to_project(project_record),
            group_chat_id=group_chat_id,
            carried_message_count=carried_message_count,
            pending_task_proposal=None,
        )

    async def wake_dashboard(
        self, *, project_id: str, user_id: int
    ) -> WakeDashboard:
        """唤醒仪表盘：返回群聊与需置顶的阻塞/重对齐任务"""
        project = await self.get_project(project_id=project_id, user_id=user_id)
        pinned_tasks = await self._repository.list_tasks_by_statuses(
            project_id=project_id,
            user_id=user_id,
            statuses=(WorkshopTaskStatus.BLOCKED, WorkshopTaskStatus.RE_ALIGNING),
        )
        return WakeDashboard(
            group_chat_id=project.group_chat_id,
            pinned_task_ids=tuple(task.id for task in pinned_tasks),
        )

    async def assign_to_task(
        self,
        *,
        project_id: str,
        user_id: int,
        task_id: str,
        expert_id: str,
    ) -> None:
        """将名册专家分配到任务（不等于移出名册）"""
        try:
            await self._repository.assign_task_expert(
                project_id=project_id,
                user_id=user_id,
                task_id=task_id,
                expert_id=expert_id,
            )
        except WorkshopExpertNotFoundError as exc:
            raise WorkshopProjectError(f"expert not on roster: {expert_id}") from exc

    async def kick_from_task(
        self,
        *,
        project_id: str,
        user_id: int,
        task_id: str,
        expert_id: str,
    ) -> None:
        """任务离场，不影响名册"""
        await self._repository.unassign_task_expert(
            project_id=project_id,
            user_id=user_id,
            task_id=task_id,
            expert_id=expert_id,
        )

    async def task_assignments(
        self, *, project_id: str, user_id: int, task_id: str
    ) -> set[str]:
        """查询任务上的专家分配"""
        return await self._repository.list_task_experts(
            project_id=project_id, user_id=user_id, task_id=task_id
        )

    async def remove_from_roster(
        self, *, project_id: str, user_id: int, expert_id: str
    ) -> None:
        """移出项目名册，并清理任务分配与房间成员"""
        try:
            await self._repository.remove_expert(
                project_id=project_id, user_id=user_id, expert_id=expert_id
            )
        except WorkshopExpertNotFoundError as exc:
            raise WorkshopProjectError(f"expert not on roster: {expert_id}") from exc
