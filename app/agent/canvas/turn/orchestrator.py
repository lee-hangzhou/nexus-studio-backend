"""Canvas turn thin entry: lock + CanvasMount + stream_agent_turn."""

from __future__ import annotations

import asyncio
from typing import AsyncIterator
from uuid import uuid4

from langgraph.types import Command

from app.agent.canvas.mount import CANVAS_AGENT_MODEL_KEY, CANVAS_MOUNT, CanvasMountContext
from app.agent.canvas.turn.lock import project_turn_lock
from app.agent.chat.stream.encoder import encode_sse_frame
from app.agent.chat.stream.frames import StreamFrameType, create_stream_frame
from app.agent.runtime.checkpointer import get_chat_checkpointer
from app.agent.runtime.turn.runner import stream_agent_turn
from app.server.chat.domain.stream_enums import StreamErrorCode
from app.server.exceptions.base import AppError
from app.server.exceptions.codes import ErrorCode
from app.server.infra.logger import bind_context, log_exception, logger


async def stream_canvas_turn(
    *,
    project_id: int,
    user_id: int,
    content: str,
    model_key: str,
    client_turn_id: str | None,
    mode: str = "auto",
    enable_tools: bool,
    cancel_event: asyncio.Event,
    turn_id: str | None = None,
    lock_held: bool = False,
) -> AsyncIterator[str]:
    turn_id = turn_id or uuid4().hex
    bind_context(user_id=user_id, project_id=project_id, turn_id=turn_id)
    requested_model_key = model_key
    acquired = False
    try:
        if not lock_held:
            await project_turn_lock.acquire(project_id, turn_id)
            acquired = True

        logger.info(
            "canvas.turn.start",
            project_id=project_id,
            turn_id=turn_id,
            mode=mode,
            model_key=CANVAS_AGENT_MODEL_KEY,
            requested_model_key=requested_model_key,
            enable_tools=enable_tools,
        )
        ctx = CanvasMountContext(
            user_id=user_id,
            conversation_id=project_id,
            turn_id=turn_id,
            cancel_event=cancel_event,
            checkpointer=get_chat_checkpointer(),
            content=content,
            client_turn_id=client_turn_id,
            mode=mode,
            enable_tools=enable_tools,
            is_resume=False,
        )
        async for chunk in stream_agent_turn(CANVAS_MOUNT, ctx):
            yield chunk
    except AppError as exc:
        code = {
            int(ErrorCode.CANVAS_PROJECT_BUSY): "canvas_project_busy",
            int(ErrorCode.CANVAS_DUPLICATE_TURN): "canvas_duplicate_turn",
        }.get(exc.code, "internal")
        yield encode_sse_frame(
            create_stream_frame(
                type=StreamFrameType.ERROR,
                code=code,
                message=exc.message,
                turn_id=turn_id,
                data=exc.details or {},
            )
        )
    except Exception as exc:
        log_exception(
            "canvas.turn.error",
            exc=exc,
            project_id=project_id,
            turn_id=turn_id,
        )
        yield encode_sse_frame(
            create_stream_frame(
                type=StreamFrameType.ERROR,
                code=StreamErrorCode.INTERNAL.value,
                message="turn failed",
                turn_id=turn_id,
            )
        )
    finally:
        if acquired:
            await project_turn_lock.release(project_id, turn_id)


async def stream_canvas_resume(
    *,
    project_id: int,
    user_id: int,
    turn_id: str,
    tool_call_id: str,
    action: str,
    cancel_event: asyncio.Event,
    lock_held: bool = False,
) -> AsyncIterator[str]:
    del tool_call_id
    acquired = False
    try:
        if not lock_held:
            await project_turn_lock.acquire(project_id, turn_id)
            acquired = True

        decision = (
            {"type": "approve"}
            if action == "confirm"
            else {"type": "reject", "message": "user rejected tool execution"}
        )
        ctx = CanvasMountContext(
            user_id=user_id,
            conversation_id=project_id,
            turn_id=turn_id,
            cancel_event=cancel_event,
            checkpointer=get_chat_checkpointer(),
            mode="manual",
            enable_tools=True,
            is_resume=True,
        )
        async for chunk in stream_agent_turn(
            CANVAS_MOUNT,
            ctx,
            resume_command=Command(resume={"decisions": [decision]}),
            is_resume=True,
        ):
            yield chunk
    finally:
        if acquired:
            await project_turn_lock.release(project_id, turn_id)
