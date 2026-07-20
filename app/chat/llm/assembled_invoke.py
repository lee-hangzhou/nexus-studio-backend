from __future__ import annotations

from typing import List

from langchain_core.messages import AIMessage, BaseMessage

from app.chat.llm.gateway_chat_model import GatewayChatModel
from app.chat.llm.stream_to_message import stream_to_ai_message


async def ainvoke_with_stream_assembly(
    llm: GatewayChatModel,
    messages: List[BaseMessage],
) -> AIMessage:
    return await stream_to_ai_message(llm, messages)
