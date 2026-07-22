from typing import List, Sequence

from langchain_core.chat_history import BaseChatMessageHistory
from langchain_core.messages import BaseMessage, messages_from_dict, messages_to_dict

from app.server.chat.domain.enums import ChatMessageRole
from app.server.chat.persistence.messages import ChatMessages


class TortoiseChatMessageHistory(BaseChatMessageHistory):
    def __init__(self, conversation_id: int, user_id: int) -> None:
        self.conversation_id = conversation_id
        self.user_id = user_id

    async def aget_messages(self) -> List[BaseMessage]:
        rows = await ChatMessages.filter(conversation_id=self.conversation_id).order_by("created_at")
        messages: List[BaseMessage] = []
        for row in rows:
            if row.payload:
                payload = row.payload if isinstance(row.payload, list) else [row.payload]
                messages.extend(messages_from_dict(payload))
        return messages

    async def aadd_messages(self, messages: Sequence[BaseMessage]) -> None:
        for message in messages:
            role = ChatMessageRole.ASSISTANT
            if message.type == "human":
                role = ChatMessageRole.USER
            elif message.type == "system":
                role = ChatMessageRole.SYSTEM
            elif message.type == "tool":
                role = ChatMessageRole.TOOL

            content = str(message.content) if message.content is not None else ""
            await ChatMessages.create(
                conversation_id=self.conversation_id,
                user_id=self.user_id,
                role=int(role),
                content=content,
                payload=messages_to_dict([message]),
                metadata={},
            )

    async def aclear(self) -> None:
        await ChatMessages.filter(conversation_id=self.conversation_id).delete()

    def clear(self) -> None:
        """LangChain 同步接口；本实现仅支持异步上下文。"""
        raise RuntimeError("TortoiseChatMessageHistory.clear() is async-only, use aclear()")
