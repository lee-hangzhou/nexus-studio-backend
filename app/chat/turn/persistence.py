from langchain_core.messages import AIMessage, HumanMessage, ToolMessage, messages_to_dict

from app.chat.attachments.service import chat_attachment_service
from app.chat.tools.lc_tools import ChatToolContext
from app.contracts.metadata import (
    ArtifactMetadata,
    AssistantMessageMetadata,
    ToolRequestMetadata,
    ToolResultMetadata,
    UserMessageMetadata,
)
from app.domain.chat_enums import ChatMessageRole
from app.models.chat_messages import ChatMessages


class TurnPersistence:
    def __init__(self, *, user_id: int, conversation_id: int) -> None:
        self.user_id = user_id
        self.conversation_id = conversation_id
        self.message_ids: list[int] = []

    async def persist_tool_request(
        self,
        *,
        turn_id: str,
        step_index: int,
        ai_message: AIMessage,
        metadata: ToolRequestMetadata,
    ) -> int:
        row = await ChatMessages.create(
            conversation_id=self.conversation_id,
            user_id=self.user_id,
            role=int(ChatMessageRole.ASSISTANT),
            content=str(ai_message.content or ""),
            payload=messages_to_dict([ai_message]),
            metadata={
                **metadata.model_dump(mode="json", exclude_none=True),
                "turn_id": turn_id,
                "step_index": step_index,
                "phase": "tool_request",
            },
        )
        self.message_ids.append(row.id)
        return row.id

    async def persist_tool_result(
        self,
        *,
        turn_id: str,
        tool_message: ToolMessage,
        meta: ToolResultMetadata,
    ) -> int:
        row = await ChatMessages.create(
            conversation_id=self.conversation_id,
            user_id=self.user_id,
            role=int(ChatMessageRole.TOOL),
            content=str(tool_message.content or ""),
            payload=messages_to_dict([tool_message]),
            metadata={
                **meta.model_dump(mode="json", exclude_none=True),
                "turn_id": turn_id,
                "result_char_count": len(str(tool_message.content or "")),
                "result_preview": str(tool_message.content or "")[:500],
            },
        )
        self.message_ids.append(row.id)
        return row.id

    async def persist_final_assistant(
        self,
        *,
        turn_id: str,
        content: str,
        ai_message: AIMessage,
        metadata: AssistantMessageMetadata,
    ) -> int:
        row = await ChatMessages.create(
            conversation_id=self.conversation_id,
            user_id=self.user_id,
            role=int(ChatMessageRole.ASSISTANT),
            content=content,
            payload=messages_to_dict([ai_message]),
            metadata={
                **metadata.model_dump(mode="json", exclude_none=True),
                "turn_id": turn_id,
                "phase": "final",
            },
        )
        self.message_ids.append(row.id)
        return row.id

    async def persist_gate_suspended_result(
        self,
        *,
        turn_id: str,
        call_id: str,
        content: str,
        meta: ToolResultMetadata,
    ) -> int:
        tool_message = ToolMessage(
            content=content,
            tool_call_id=call_id,
            name=meta.name or "request_user_gate",
        )
        return await self.persist_tool_result(
            turn_id=turn_id,
            tool_message=tool_message,
            meta=meta.model_copy(update={"synthetic": True}),
        )

    async def persist_synthetic_tool_error(
        self,
        *,
        turn_id: str,
        call_id: str,
        name: str,
        body: str,
        meta: ToolResultMetadata,
    ) -> int:
        tool_message = ToolMessage(content=body, tool_call_id=call_id, name=name)
        return await self.persist_tool_result(
            turn_id=turn_id,
            tool_message=tool_message,
            meta=meta.model_copy(
                update={"name": name, "call_id": call_id, "synthetic": True}
            ),
        )

    async def persist_cancelled_close(self, *, turn_id: str, step_index: int) -> int:
        ai = AIMessage(content="（本轮已停止）")
        row = await ChatMessages.create(
            conversation_id=self.conversation_id,
            user_id=self.user_id,
            role=int(ChatMessageRole.ASSISTANT),
            content=ai.content,
            payload=messages_to_dict([ai]),
            metadata={
                "turn_id": turn_id,
                "step_index": step_index,
                "phase": "cancelled",
                "turn_status": "cancelled",
                "cancelled": True,
            },
        )
        self.message_ids.append(row.id)
        return row.id

    async def persist_turn_error(
        self,
        *,
        turn_id: str,
        step_index: int,
        content: str,
        error_code: str | None = None,
    ) -> int:
        ai = AIMessage(content=content)
        row = await ChatMessages.create(
            conversation_id=self.conversation_id,
            user_id=self.user_id,
            role=int(ChatMessageRole.ASSISTANT),
            content=content,
            payload=messages_to_dict([ai]),
            metadata={
                "turn_id": turn_id,
                "step_index": step_index,
                "phase": "final",
                "error": error_code or "turn_failed",
            },
        )
        self.message_ids.append(row.id)
        return row.id


async def persist_user_message(
    *,
    user_id: int,
    conversation_id: int,
    content: str,
    turn_human: HumanMessage,
    attachment_ids: list[int],
    turn_id: str,
    client_turn_id: str | None,
) -> ChatMessages:
    metadata = UserMessageMetadata(
        turn_id=turn_id,
        client_turn_id=client_turn_id,
        attachment_ids=attachment_ids,
    )
    row = await ChatMessages.create(
        conversation_id=conversation_id,
        user_id=user_id,
        role=int(ChatMessageRole.USER),
        content=content,
        payload=messages_to_dict([turn_human]),
        metadata=metadata.model_dump(mode="json", exclude_none=True),
    )
    await chat_attachment_service.bind_to_message(
        user_id=user_id,
        conversation_id=conversation_id,
        attachment_ids=attachment_ids,
        message_id=row.id,
    )
    return row


async def finalize_published_deliverables(
    *,
    persistence: TurnPersistence,
    ctx: ChatToolContext,
    turn_id: str,
    metadata: AssistantMessageMetadata,
    user_id: int,
    conversation_id: int,
) -> int | None:
    """Persist a final assistant message when tools published files but the model left no text reply."""
    if not ctx.published_artifacts:
        return None
    names = ", ".join(item.filename for item in ctx.published_artifacts)
    content = f"已完成，生成文件：{names}"
    return await finalize_assistant(
        persistence=persistence,
        ctx=ctx,
        turn_id=turn_id,
        content=content,
        ai_message=AIMessage(content=content),
        metadata=metadata,
        user_id=user_id,
        conversation_id=conversation_id,
    )


async def finalize_assistant(
    *,
    persistence: TurnPersistence,
    ctx: ChatToolContext,
    turn_id: str,
    content: str,
    ai_message: AIMessage,
    metadata: AssistantMessageMetadata,
    user_id: int,
    conversation_id: int,
) -> int:
    artifacts_meta = [
        ArtifactMetadata(
            attachment_id=item.attachment_id,
            filename=item.filename,
            mime_type=item.mime_type,
            role="deliverable",
            turn_id=turn_id,
        )
        for item in ctx.published_artifacts
    ]
    if artifacts_meta:
        metadata = metadata.model_copy(update={"artifacts": artifacts_meta})

    msg_id = await persistence.persist_final_assistant(
        turn_id=turn_id,
        content=content,
        ai_message=ai_message,
        metadata=metadata,
    )
    if ctx.published_artifacts:
        await chat_attachment_service.bind_to_message(
            user_id=user_id,
            conversation_id=conversation_id,
            attachment_ids=[item.attachment_id for item in ctx.published_artifacts],
            message_id=msg_id,
        )
    return msg_id
