from __future__ import annotations

from dataclasses import dataclass

from app.server.chat.persistence.conversations import ChatConversations
from app.server.chat.persistence.upgrade_invite_proposals import ChatUpgradeInviteProposals
from app.server.workshop.domain.upgrade_invite import (
    UpgradeInviteProposalError,
    UpgradeInviteProposalStatus,
    validate_upgrade_invite_proposal,
)
from app.server.workshop.services.workshop_project_service import (
    UpgradeResult,
    WorkshopProjectError,
    WorkshopProjectService,
)


class UpgradeInviteServiceError(ValueError):
    """升级邀请用例失败"""


@dataclass(frozen=True, slots=True)
class UpgradeInviteProposalRecord:
    """升级邀请提议记录"""

    id: int
    conversation_id: int
    expert_keys: tuple[str, ...]
    primary_expert_key: str
    rationale: str
    host_narration: str
    source_user_text: str
    status: UpgradeInviteProposalStatus


@dataclass(frozen=True, slots=True)
class ConfirmedUpgradeInvite:
    """确认升级后的续跑上下文；零专家时 primary_expert_id 为 None（续跑走 Host）"""

    upgrade: UpgradeResult
    primary_expert_id: str | None
    host_narration: str
    source_user_text: str


def _parse_expert_keys(raw: object) -> tuple[str, ...]:
    """JSONField 边界解析专家 key 列表；允许空列表"""
    if not isinstance(raw, list):
        raise UpgradeInviteServiceError("corrupt expert_keys")
    if not raw:
        return ()
    keys: list[str] = []
    for item in raw:
        if not isinstance(item, str) or not item.strip():
            raise UpgradeInviteServiceError("corrupt expert_keys item")
        keys.append(item)
    return tuple(keys)


def _to_record(row: ChatUpgradeInviteProposals) -> UpgradeInviteProposalRecord:
    """ORM 行转为服务记录"""
    try:
        status = UpgradeInviteProposalStatus(row.status)
    except ValueError as exc:
        raise UpgradeInviteServiceError(f"corrupt proposal status: {row.status}") from exc
    return UpgradeInviteProposalRecord(
        id=row.id,
        conversation_id=row.conversation_id,
        expert_keys=_parse_expert_keys(row.expert_keys),
        primary_expert_key=row.primary_expert_key,
        rationale=row.rationale,
        host_narration=row.host_narration,
        source_user_text=row.source_user_text,
        status=status,
    )


class UpgradeInviteService:
    """未升级会话的升级邀请用例"""

    def __init__(self, projects: WorkshopProjectService) -> None:
        """注入工坊项目服务"""
        self._projects = projects

    async def create_proposal(
        self,
        *,
        user_id: int,
        conversation_id: int,
        turn_id: str,
        source_user_text: str,
        expert_keys: tuple[str, ...],
        primary_expert_key: str,
        rationale: str,
        host_narration: str,
    ) -> UpgradeInviteProposalRecord:
        """创建待确认的升级邀请提议并作废同会话旧 pending"""
        try:
            validated = validate_upgrade_invite_proposal(
                expert_keys=expert_keys,
                primary_expert_key=primary_expert_key,
                rationale=rationale,
                host_narration=host_narration,
            )
        except UpgradeInviteProposalError as exc:
            raise UpgradeInviteServiceError(str(exc)) from exc

        conversation = await ChatConversations.get_or_none(
            id=conversation_id, user_id=user_id
        )
        if conversation is None:
            raise UpgradeInviteServiceError("conversation not found")

        existing = await self._projects.get_project_by_group_chat(
            user_id=user_id, group_chat_id=conversation_id
        )
        if existing is not None:
            raise UpgradeInviteServiceError("conversation already upgraded")

        await ChatUpgradeInviteProposals.filter(
            conversation_id=conversation_id,
            user_id=user_id,
            status=UpgradeInviteProposalStatus.PENDING,
        ).update(status=UpgradeInviteProposalStatus.SUPERSEDED)

        row = await ChatUpgradeInviteProposals.create(
            conversation_id=conversation_id,
            user_id=user_id,
            expert_keys=list(validated.expert_keys),
            primary_expert_key=validated.primary_expert_key,
            rationale=validated.rationale,
            host_narration=validated.host_narration,
            source_user_text=source_user_text,
            status=UpgradeInviteProposalStatus.PENDING,
            turn_id=turn_id,
        )
        return _to_record(row)

    @staticmethod
    async def get_pending(
        *, user_id: int, conversation_id: int
    ) -> UpgradeInviteProposalRecord | None:
        """读取会话当前 pending 升级邀请；无则 None"""
        row = await ChatUpgradeInviteProposals.get_or_none(
            conversation_id=conversation_id,
            user_id=user_id,
            status=UpgradeInviteProposalStatus.PENDING,
        )
        if row is None:
            return None
        return _to_record(row)

    @staticmethod
    async def conversation_ids_with_pending(
        *, user_id: int, conversation_ids: list[int]
    ) -> set[int]:
        """批量查询仍有 pending 升级邀请的会话 id"""
        if not conversation_ids:
            return set()
        rows = await ChatUpgradeInviteProposals.filter(
            user_id=user_id,
            conversation_id__in=conversation_ids,
            status=UpgradeInviteProposalStatus.PENDING,
        ).values_list("conversation_id", flat=True)
        return {int(cid) for cid in rows}

    async def decline(
        self, *, user_id: int, conversation_id: int, proposal_id: int
    ) -> None:
        """拒绝提议并将会话标为已拒绝主动升级"""
        row = await ChatUpgradeInviteProposals.get_or_none(
            id=proposal_id, user_id=user_id, conversation_id=conversation_id
        )
        if row is None:
            raise UpgradeInviteServiceError("proposal not found")
        if row.status != UpgradeInviteProposalStatus.PENDING:
            raise UpgradeInviteServiceError(f"proposal not pending: {row.status}")
        row.status = UpgradeInviteProposalStatus.DECLINED
        await row.save(update_fields=["status", "updated_at"])
        updated = await ChatConversations.filter(id=conversation_id, user_id=user_id).update(
            upgrade_invite_declined=True,
            active_turn_id=None,
            active_turn_started_at=None,
        )
        if updated != 1:
            raise UpgradeInviteServiceError("conversation not found")

    async def confirm(
        self,
        *,
        user_id: int,
        conversation_id: int,
        proposal_id: int,
        expert_keys: tuple[str, ...],
        primary_expert_key: str,
        project_name: str,
        carried_message_count: int,
    ) -> ConfirmedUpgradeInvite:
        """确认升级进房并写入 Host 说明消息"""
        row = await ChatUpgradeInviteProposals.get_or_none(
            id=proposal_id, user_id=user_id, conversation_id=conversation_id
        )
        if row is None:
            raise UpgradeInviteServiceError("proposal not found")
        if row.status != UpgradeInviteProposalStatus.PENDING:
            raise UpgradeInviteServiceError(f"proposal not pending: {row.status}")

        try:
            validated = validate_upgrade_invite_proposal(
                expert_keys=expert_keys,
                primary_expert_key=primary_expert_key,
                rationale=row.rationale,
                host_narration=row.host_narration,
            )
        except UpgradeInviteProposalError as exc:
            raise UpgradeInviteServiceError(str(exc)) from exc

        proposed_keys = set(_parse_expert_keys(row.expert_keys))
        if any(key not in proposed_keys for key in validated.expert_keys):
            raise UpgradeInviteServiceError(
                "confirm expert_keys must be a subset of the pending proposal"
            )

        try:
            if validated.expert_keys:
                upgrade = await self._projects.confirm_upgrade_with_experts(
                    user_id=user_id,
                    group_chat_id=conversation_id,
                    project_name=project_name,
                    carried_message_count=carried_message_count,
                    expert_keys=validated.expert_keys,
                )
            else:
                upgrade = await self._projects.confirm_upgrade_to_project(
                    user_id=user_id,
                    group_chat_id=conversation_id,
                    project_name=project_name,
                    carried_message_count=carried_message_count,
                    initial_expert_keys=(),
                )
        except WorkshopProjectError as exc:
            raise UpgradeInviteServiceError(str(exc)) from exc

        primary_id: str | None = None
        if validated.expert_keys:
            roster = await self._projects.list_roster(
                project_id=upgrade.project.id, user_id=user_id
            )
            primary_id = next(
                (
                    expert.id
                    for expert in roster
                    if expert.preset_key == validated.primary_expert_key
                ),
                None,
            )
            if primary_id is None:
                raise UpgradeInviteServiceError(
                    f"primary expert not on roster: {validated.primary_expert_key}"
                )

        row.status = UpgradeInviteProposalStatus.CONFIRMED
        row.expert_keys = list(validated.expert_keys)
        row.primary_expert_key = validated.primary_expert_key
        await row.save(
            update_fields=["status", "expert_keys", "primary_expert_key", "updated_at"]
        )
        await ChatConversations.filter(id=conversation_id, user_id=user_id).update(
            upgrade_invite_declined=False,
            active_turn_id=None,
            active_turn_started_at=None,
        )

        # Host 旁白由提议时 LLM 写入提案；确认只落库，不拼模板
        host_narration = validated.host_narration
        from app.server.chat.domain.enums import ChatMessageRole
        from app.server.chat.persistence.messages import ChatMessages

        await ChatMessages.create(
            conversation_id=conversation_id,
            user_id=user_id,
            role=ChatMessageRole.ASSISTANT,
            content=host_narration,
            payload={},
            metadata={
                "speaker_role": "host",
                "expert_name": "项目助手",
                "avatar": "/avatars/experts/host.png",
            },
        )
        return ConfirmedUpgradeInvite(
            upgrade=upgrade,
            primary_expert_id=primary_id,
            host_narration=host_narration,
            source_user_text=row.source_user_text,
        )
