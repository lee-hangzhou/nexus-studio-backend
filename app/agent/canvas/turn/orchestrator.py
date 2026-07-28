from __future__ import annotations

import asyncio
from typing import AsyncIterator
from uuid import uuid4

from langgraph.types import Command

from app.agent.canvas.mount import CANVAS_MOUNT, CanvasMountContext
from app.agent.canvas.turn.lock import canvas_turn_lock
from app.agent.canvas.turn.pending import (
    APPLY_CANVAS_PATCH,
    SUBMIT_NODE_GENERATION,
    edited_tool_args_from_pending_operation,
)
from app.agent.chat.stream.encoder import encode_sse_frame
from app.agent.chat.stream.frames import StreamFrameType, create_stream_frame
from app.agent.runtime.checkpointer import get_chat_checkpointer
from app.agent.runtime.tools.user_skill_protocol import (
    SKILL_WRITE_OPERATION_TYPE,
    WRITE_USER_SKILL_FILE,
)
from app.agent.runtime.turn.runner import stream_agent_turn
from app.contracts.turn_content import TurnUserInput
from app.server.canvas.domain.enums import CanvasPendingOperationType
from app.server.chat.domain.stream_enums import StreamErrorCode
from app.server.exceptions.base import AppError
from app.server.exceptions.codes import ErrorCode
from app.server.infra.logger import bind_context, log_exception, logger
from app.server.ports.product import SelectedSkillDTO


async def stream_canvas_turn(
    *,
    project_id: int,
    episode_id: int,
    session_id: int,
    user_id: int,
    content_text: str,
    user_input: TurnUserInput,
    selected_skills: tuple[SelectedSkillDTO, ...],
    model_key: str,
    client_turn_id: str | None,
    mode: str = "auto",
    enable_tools: bool,
    cancel_event: asyncio.Event,
    turn_id: str | None = None,
    lock_held: bool = False,
) -> AsyncIterator[str]:
    turn_id = turn_id or uuid4().hex
    bind_context(
        user_id=user_id,
        project_id=project_id,
        episode_id=episode_id,
        session_id=session_id,
        turn_id=turn_id,
    )
    acquired = False
    try:
        if not lock_held:
            await canvas_turn_lock.acquire(session_id, turn_id)
            acquired = True

        ctx = CanvasMountContext(
            user_id=user_id,
            project_id=project_id,
            episode_id=episode_id,
            session_id=session_id,
            turn_id=turn_id,
            cancel_event=cancel_event,
            checkpointer=get_chat_checkpointer(),
            model_key=model_key,
            content_text=content_text,
            user_input=user_input,
            selected_skills=selected_skills,
            client_turn_id=client_turn_id,
            mode=mode,
            enable_tools=enable_tools,
            is_resume=False,
        )
        logger.info(
            "canvas.turn.start",
            project_id=project_id,
            episode_id=episode_id,
            session_id=session_id,
            turn_id=turn_id,
            mode=mode,
            model_key=ctx.resolved_model_key,
            enable_tools=enable_tools,
        )
        async for chunk in stream_agent_turn(CANVAS_MOUNT, ctx):
            yield chunk
    except AppError as exc:
        code = {
            int(ErrorCode.CANVAS_SESSION_BUSY): StreamErrorCode.CANVAS_SESSION_BUSY.value,
            int(ErrorCode.CANVAS_EPISODE_BUSY): StreamErrorCode.CANVAS_EPISODE_BUSY.value,
            int(ErrorCode.CANVAS_DUPLICATE_TURN): StreamErrorCode.CANVAS_DUPLICATE_TURN.value,
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
            episode_id=episode_id,
            session_id=session_id,
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
            await canvas_turn_lock.release(session_id, turn_id)


async def stream_canvas_resume(
    *,
    project_id: int,
    episode_id: int,
    session_id: int,
    user_id: int,
    turn_id: str,
    tool_call_id: str,
    action: str,
    cancel_event: asyncio.Event,
    lock_held: bool = False,
    model_key: str = "",
    operation: dict | None = None,
) -> AsyncIterator[str]:
    """恢复画布 turn；确认时带结构化 operation 映射为 edited_action"""
    del tool_call_id
    acquired = False
    try:
        if not lock_held:
            await canvas_turn_lock.acquire(session_id, turn_id)
            acquired = True

        decision: dict
        if action == "confirm":
            decision = _confirm_decision(operation)
        else:
            decision = {"type": "reject", "message": "user rejected tool execution"}
        ctx = CanvasMountContext(
            user_id=user_id,
            project_id=project_id,
            episode_id=episode_id,
            session_id=session_id,
            turn_id=turn_id,
            cancel_event=cancel_event,
            checkpointer=get_chat_checkpointer(),
            model_key=model_key,
            mode="manual",
            enable_tools=True,
            is_resume=True,
            client_turn_id=turn_id,
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
            await canvas_turn_lock.release(session_id, turn_id)


def _confirm_decision(operation: dict | None) -> dict:
    """构建确认决策；带 operation 时映射为 edited_action.args；未知类型显式 reject"""
    if not isinstance(operation, dict):
        return {"type": "reject", "message": "confirm requires structured operation"}
    op_type = operation.get("type")
    if op_type == SKILL_WRITE_OPERATION_TYPE:
        if operation.get("revision_invalid"):
            return {"type": "reject", "message": "invalid skill write revision"}
        try:
            args = edited_tool_args_from_pending_operation(WRITE_USER_SKILL_FILE, operation)
        except (ValueError, TypeError) as exc:
            return {"type": "reject", "message": str(exc)}
        return {
            "type": "approve",
            "edited_action": {"name": WRITE_USER_SKILL_FILE, "args": args},
        }
    if op_type in {CanvasPendingOperationType.CREATE, CanvasPendingOperationType.UPDATE}:
        try:
            args = edited_tool_args_from_pending_operation(APPLY_CANVAS_PATCH, operation)
        except (ValueError, TypeError) as exc:
            return {"type": "reject", "message": str(exc)}
        return {
            "type": "approve",
            "edited_action": {"name": APPLY_CANVAS_PATCH, "args": args},
        }
    if op_type == CanvasPendingOperationType.GENERATE:
        try:
            args = edited_tool_args_from_pending_operation(SUBMIT_NODE_GENERATION, operation)
        except (ValueError, TypeError) as exc:
            return {"type": "reject", "message": str(exc)}
        return {
            "type": "approve",
            "edited_action": {"name": SUBMIT_NODE_GENERATION, "args": args},
        }
    return {"type": "reject", "message": f"unknown pending operation type: {op_type!r}"}
