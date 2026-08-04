"""创作提示词助手 turn：PROMPT_ASSISTANT_MOUNT + 会话生命周期壳。"""

from __future__ import annotations

import asyncio
from typing import AsyncIterator

from app.agent.chat.turn.conversation_agent_stream import stream_conversation_agent_turn
from app.agent.prompt_assistant.mount import PROMPT_ASSISTANT_MOUNT, PromptAssistantMountContext
from app.agent.runtime.checkpointer import get_chat_checkpointer
from app.contracts.composer_prompt import GenerateComposerContext
from app.contracts.turn_content import TurnUserInput
from app.server.chat.persistence.conversations import ChatConversations


async def stream_prompt_assistant_turn(
    *,
    conversation: ChatConversations,
    user_id: int,
    conversation_id: int,
    user_input: TurnUserInput,
    content_text: str,
    model_key: str,
    enable_tools: bool,
    client_turn_id: str | None,
    cancel_event: asyncio.Event,
    turn_id: str,
    composer_context: GenerateComposerContext | None,
) -> AsyncIterator[str]:
    """执行创作提示词助手一轮流式 turn。"""
    ctx = PromptAssistantMountContext(
        user_id=user_id,
        conversation_id=conversation_id,
        turn_id=turn_id,
        cancel_event=cancel_event,
        checkpointer=get_chat_checkpointer(),
        conversation=conversation,
        content=content_text,
        user_input=user_input,
        model_key=model_key,
        enable_tools=enable_tools,
        client_turn_id=client_turn_id,
        composer_context=composer_context,
    )
    async for chunk in stream_conversation_agent_turn(
        mount=PROMPT_ASSISTANT_MOUNT,
        ctx=ctx,
        conversation=conversation,
        user_id=user_id,
        conversation_id=conversation_id,
        turn_id=turn_id,
        cancel_event=cancel_event,
        log_event_prefix="prompt_assistant.stream_turn",
    ):
        yield chunk
