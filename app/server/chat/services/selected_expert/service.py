from __future__ import annotations

from app.contracts.workshop import (
    ChatSelectedExpertView,
    ExpertDirectoryResponse,
    ExpertTeamDirectoryResponse,
    WorkshopConnectorDirectoryResponse,
    WorkshopConnectorEntry,
    WorkshopUpgradeFromTeamRequest,
    WorkshopUpgradeFromExpertRequest,
    WorkshopUpgradeResultView,
)
from app.server.chat.domain.enums import ChatConversationStatus
from app.server.chat.persistence.conversations import ChatConversations
from app.server.exceptions.base import AppError
from app.server.exceptions.codes import ErrorCode
from app.server.workshop.assembly import (
    expert_directory_to_view,
    expert_teams_to_view,
    upgrade_to_view,
)
from app.server.workshop.domain.connectors import (
    PLATFORM_CONNECTORS,
    merge_connector_statuses,
)
from app.server.workshop.domain.expert_catalog import get_catalog_entry
from app.server.workshop.services.workshop_project_service import (
    WorkshopProjectError,
    WorkshopProjectService,
)


class ChatSelectedExpertService:
    """会话级专家选择应用服务"""

    def __init__(self, workshop_projects: WorkshopProjectService) -> None:
        """初始化"""
        self._workshop_projects = workshop_projects

    async def _owned_conversation(self, user_id: int, conversation_id: int) -> ChatConversations:
        """校验并加载用户拥有的会话"""
        row = await ChatConversations.get_or_none(id=conversation_id, user_id=user_id)
        if row is None or row.status != int(ChatConversationStatus.ACTIVE):
            raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "conversation not found")
        return row

    def _to_view(self, conversation: ChatConversations) -> ChatSelectedExpertView:
        """会话选中专家转视图"""
        key = conversation.selected_expert_key
        if not key:
            return ChatSelectedExpertView(
                conversation_id=int(conversation.id),
                expert_key=None,
                name=None,
                avatar_id=None,
            )
        entry = get_catalog_entry(key)
        return ChatSelectedExpertView(
            conversation_id=int(conversation.id),
            expert_key=entry.key,
            name=entry.name,
            avatar_id=entry.avatar_id,
        )

    async def get_selected_expert(
        self, *, user_id: int, conversation_id: int
    ) -> ChatSelectedExpertView:
        """读取会话当前选中专家"""
        conversation = await self._owned_conversation(user_id, conversation_id)
        return self._to_view(conversation)

    async def set_selected_expert(
        self, *, user_id: int, conversation_id: int, expert_key: str
    ) -> ChatSelectedExpertView:
        """选择单个专家"""
        get_catalog_entry(expert_key)
        if expert_key == "host":
            raise AppError(ErrorCode.INVALID_PARAMS, "host cannot be selected in single-agent chat")
        conversation = await self._owned_conversation(user_id, conversation_id)
        conversation.selected_expert_key = expert_key
        await conversation.save(update_fields=["selected_expert_key", "updated_at"])
        return self._to_view(conversation)

    async def clear_selected_expert(
        self, *, user_id: int, conversation_id: int
    ) -> ChatSelectedExpertView:
        """清除会话选中专家"""
        conversation = await self._owned_conversation(user_id, conversation_id)
        conversation.selected_expert_key = None
        await conversation.save(update_fields=["selected_expert_key", "updated_at"])
        return self._to_view(conversation)

    def list_expert_directory(self) -> ExpertDirectoryResponse:
        """用户可见专家目录"""
        return expert_directory_to_view()

    def list_expert_teams(self) -> ExpertTeamDirectoryResponse:
        """用户可见专家团队目录"""
        return expert_teams_to_view()


    async def list_connectors(
        self, *, user_id: int, project_id: str | None = None
    ) -> WorkshopConnectorDirectoryResponse:
        """连接器目录：状态来自项目 data_sources，无项目则全部 disconnected"""
        statuses = {c.key: "disconnected" for c in PLATFORM_CONNECTORS}
        if project_id:
            sources = await self._workshop_projects.get_data_sources(
                project_id=project_id, user_id=user_id
            )
            source_map = {s.key: s.status for s in sources.sources}
            if sources.shop is not None:
                source_map["shop"] = sources.shop.status
            statuses = merge_connector_statuses(source_map)
        items = [
            WorkshopConnectorEntry(
                key=c.key,
                name=c.name,
                description=c.description,
                status=statuses[c.key],  # type: ignore[arg-type]
            )
            for c in PLATFORM_CONNECTORS
        ]
        return WorkshopConnectorDirectoryResponse(items=items)

    async def upgrade_from_team(
        self, *, user_id: int, body: WorkshopUpgradeFromTeamRequest
    ) -> WorkshopUpgradeResultView:
        """团队升级已停用"""
        del user_id, body
        raise AppError(ErrorCode.INVALID_PARAMS, "不支持团队升级")

    async def upgrade_from_expert(
        self, *, user_id: int, body: WorkshopUpgradeFromExpertRequest
    ) -> WorkshopUpgradeResultView:
        """单专家邀请升级：名册仅该专家并进入房间"""
        existing = await self._workshop_projects.get_project_by_group_chat(
            user_id=user_id,
            group_chat_id=body.conversation_id,
        )
        if existing is not None:
            raise AppError(ErrorCode.WORKSHOP_CONFLICT, "group chat already has workshop project")
        await self._owned_conversation(user_id, body.conversation_id)
        get_catalog_entry(body.expert_key)
        try:
            result = await self._workshop_projects.confirm_upgrade_from_expert(
                user_id=user_id,
                group_chat_id=body.conversation_id,
                project_name=body.project_name,
                seed_goal=body.seed_goal,
                carried_message_count=body.carried_message_count,
                expert_key=body.expert_key,
            )
        except WorkshopProjectError as exc:
            raise AppError(ErrorCode.WORKSHOP_CONFLICT, str(exc)) from exc
        conversation = await self._owned_conversation(user_id, body.conversation_id)
        # 升级后默认发给项目助手；用户再通过「发给」点选在场专家
        conversation.selected_expert_key = None
        await conversation.save(update_fields=["selected_expert_key", "updated_at"])
        return upgrade_to_view(result)
