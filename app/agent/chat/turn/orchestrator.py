"""Chat turn SSE: lock / lifecycle shell over ChatMount + stream_agent_turn."""

from __future__ import annotations

import asyncio
import contextlib
from typing import AsyncIterator

from app.agent.chat.gate import turn_auth as turn_auth_store
from app.agent.chat.mount import CHAT_MOUNT, ChatMountContext
from app.agent.chat.stream.encoder import encode_sse_frame
from app.agent.chat.stream.frames import StreamFrameType, create_stream_frame
from app.agent.chat.turn.abort import finalize_inflight_turn_abort
from app.agent.chat.turn.cancel_watch import watch_turn_cancel
from app.agent.chat.turn.lifecycle import clear_turn_active, mark_turn_active, touch_conversation_updated
from app.agent.chat.turn.lock import conversation_turn_lock
from app.agent.chat.turn.observation_store import clear_turn_observation_snapshot
from app.agent.chat.turn.trace import bind_turn_trace
from app.agent.chat.turn.usage_finalize import finalize_observation_segment
from app.agent.runtime.checkpointer import get_chat_checkpointer
from app.agent.runtime.turn.runner import stream_agent_turn
from app.server.chat.domain.stream_enums import StreamErrorCode
from app.server.chat.persistence.conversations import ChatConversations
from app.server.infra.logger import bind_context, log_exception, logger


async def stream_turn(
    *,
    conversation: ChatConversations,
    user_id: int,
    conversation_id: int,
    content: str,
    model_key: str,
    attachment_ids: list[int],
    enable_tools: bool,
    client_turn_id: str | None,
    cancel_event: asyncio.Event,
    turn_id: str,
) -> AsyncIterator[str]:
    bind_context(
        user_id=user_id,
        conversation_id=conversation_id,
        turn_id=turn_id,
    )
    ctx = ChatMountContext(
        user_id=user_id,
        conversation_id=conversation_id,
        turn_id=turn_id,
        cancel_event=cancel_event,
        checkpointer=get_chat_checkpointer(),
        conversation=conversation,
        content=content,
        model_key=model_key,
        attachment_ids=attachment_ids,
        enable_tools=enable_tools,
        client_turn_id=client_turn_id,
    )
    cancel_watch_task: asyncio.Task | None = None
    last_step_index = 0
    terminated_by = "error"
    gate_interrupted = False

    try:
        await mark_turn_active(conversation, turn_id)
        cancel_watch_task = asyncio.create_task(
            watch_turn_cancel(
                conversation_id=conversation_id,
                turn_id=turn_id,
                cancel_event=cancel_event,
            )
        )
        bind_turn_trace(turn_id=turn_id, conversation_id=conversation_id)

        async for chunk in stream_agent_turn(CHAT_MOUNT, ctx):
            yield chunk

        if ctx.session is not None:
            terminated_by = ctx.session.terminated_by
            gate_interrupted = ctx.session.gate_interrupted
        if ctx.recorder is not None:
            last_step_index = ctx.recorder.last_step_index
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        log_exception(
            "chat.stream_turn.failed",
            exc=exc,
            conversation_id=conversation_id,
            turn_id=turn_id,
        )
        if ctx.usage_collector is not None:
            ctx.usage_collector.note_failure(exc)
            ctx.usage_collector.note_termination(
                terminated_by="error",
                message=str(exc) or "turn failed",
            )
        yield encode_sse_frame(
            create_stream_frame(
                type=StreamFrameType.ERROR,
                code=StreamErrorCode.INTERNAL.value,
                message="turn failed",
            )
        )
    finally:
        if cancel_watch_task is not None:
            cancel_watch_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await cancel_watch_task
        if cancel_event.is_set():
            with contextlib.suppress(Exception):
                await finalize_inflight_turn_abort(
                    user_id=user_id,
                    conversation=conversation,
                    conversation_id=conversation_id,
                    turn_id=turn_id,
                    step_index=last_step_index,
                    persistence=ctx.persistence,
                    agent=ctx.agent,
                    config=ctx.runnable_config,
                    turn_start_messages=ctx.turn_start_messages,
                    already_persisted_close=bool(ctx.recorder and ctx.recorder.final_persisted),
                )
        elif not gate_interrupted:
            try:
                await turn_auth_store.clear_turn_auth(conversation_id, turn_id)
                await clear_turn_active(conversation)
                await clear_turn_observation_snapshot(conversation_id, turn_id)
            except Exception:
                logger.exception(
                    "chat.stream_turn.clear_active_failed",
                    conversation_id=conversation_id,
                    turn_id=turn_id,
                )
        try:
            await touch_conversation_updated(conversation)
        except Exception:
            logger.exception(
                "chat.stream_turn.touch_failed",
                conversation_id=conversation_id,
                turn_id=turn_id,
            )
        try:
            if (
                ctx.guards is not None
                and ctx.usage_collector is not None
                and ctx.observation is not None
            ):
                if ctx.session is not None:
                    terminated_by = ctx.session.terminated_by
                if ctx.usage_collector.terminated_by != terminated_by:
                    ctx.usage_collector.terminated_by = terminated_by
                finalize_observation_segment(
                    ctx.observation,
                    model_steps_used=ctx.guards.model_steps_used,
                    tool_calls_used=ctx.guards.tool_calls_used,
                    wall_clock_seconds=ctx.guards.wall_clock_seconds(),
                    terminated_by=terminated_by,
                    termination_message=ctx.usage_collector.termination_message,
                )
        except Exception:
            logger.exception(
                "chat.stream_turn.usage_record_failed",
                conversation_id=conversation_id,
                turn_id=turn_id,
            )
        try:
            await conversation_turn_lock.release(conversation_id, turn_id)
        except Exception:
            logger.exception(
                "chat.stream_turn.release_failed",
                conversation_id=conversation_id,
                turn_id=turn_id,
            )
