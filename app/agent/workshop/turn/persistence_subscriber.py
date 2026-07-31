from __future__ import annotations

from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage

from app.agent.chat.tools.lc_tools import ChatToolContext
from app.agent.chat.turn.persistence import TurnPersistence, finalize_assistant, persist_user_message
from app.agent.runtime.turn_engine.events import TurnCompleted, TurnEvent, TurnEventKind, TurnFailed
from app.agent.runtime.turn_engine.subscribers import TurnEmit
from app.contracts.metadata import AssistantMessageMetadata, TurnContextMetadata


class WorkshopPersistenceSubscriber:
    """将工坊回合答案落库并写入发言归属元数据"""

    barrier_events = frozenset(
        {
            TurnEventKind.TURN_COMPLETED,
            TurnEventKind.TURN_FAILED,
        }
    )
    broadcast_events = frozenset()

    def __init__(
        self,
        *,
        persistence: TurnPersistence,
        tool_ctx: ChatToolContext,
        user_id: int,
        conversation_id: int,
        turn_id: str,
        enable_tools: bool,
        attribution: dict[str, str | None],
    ) -> None:
        """绑定持久化与发言归属"""
        self._persistence = persistence
        self._tool_ctx = tool_ctx
        self._user_id = user_id
        self._conversation_id = conversation_id
        self._turn_id = turn_id
        self._enable_tools = enable_tools
        self._attribution = attribution
        self._finalized = False

    async def handle(self, event: TurnEvent, *, emit: TurnEmit) -> None:
        """回合完成时写入助手消息"""
        del emit
        if isinstance(event, TurnFailed):
            return
        if not isinstance(event, TurnCompleted) or self._finalized:
            return
        text = (event.answer_text or "").strip()
        if not text:
            return
        metadata = AssistantMessageMetadata(
            turn_context=TurnContextMetadata(
                has_attachments=False,
                enable_tools=self._enable_tools,
            ),
            speaker_role=self._attribution.get("speaker_role"),
            expert_id=self._attribution.get("expert_id"),
            expert_name=self._attribution.get("expert_name"),
            avatar=self._attribution.get("avatar"),
            task_id=self._attribution.get("task_id"),
        )
        await finalize_assistant(
            persistence=self._persistence,
            ctx=self._tool_ctx,
            turn_id=self._turn_id,
            content=text,
            ai_message=AIMessage(content=text),
            metadata=metadata,
            user_id=self._user_id,
            conversation_id=self._conversation_id,
        )
        self._finalized = True


async def persist_workshop_user_message(
    *,
    user_id: int,
    conversation_id: int,
    content: str,
    turn_id: str,
) -> None:
    """持久化工坊回合用户消息"""
    await persist_user_message(
        user_id=user_id,
        conversation_id=conversation_id,
        content=content,
        turn_human=HumanMessage(content=content),
        bind_attachment_ids=[],
        turn_id=turn_id,
        client_turn_id=None,
        input_snapshot={},
    )


def workshop_workspace() -> Path:
    """工坊回合临时工作区根路径"""
    return Path("/tmp")
