from __future__ import annotations

from app.agent.runtime.ports import get_canvas_port
from app.contracts.metadata import CanvasMessageMetadata, CanvasToolStepMetadata
from app.server.chat.domain.enums import ChatMessageRole
from app.server.exceptions.base import AppError
from app.server.exceptions.codes import ErrorCode


async def canvas_turn_already_completed(session_id: int, client_turn_id: str) -> bool:
    """判断 client_turn_id 是否已有助手终态消息"""
    return await get_canvas_port().is_turn_completed(session_id, client_turn_id)


async def persist_canvas_user_message(
    *,
    episode_id: int,
    session_id: int,
    user_id: int,
    content: str,
    client_turn_id: str | None,
    turn_id: str,
) -> bool:
    """持久化用户消息, 返回是否新建；重复 client_turn_id 已完成则抛错。"""
    canvas = get_canvas_port()
    if client_turn_id:
        if await canvas.is_turn_completed(session_id, client_turn_id):
            raise AppError(
                ErrorCode.CANVAS_DUPLICATE_TURN,
                "canvas turn already completed",
                details={"client_turn_id": client_turn_id},
            )
        if await canvas.find_user_turn_message(session_id, client_turn_id):
            return False
    meta = CanvasMessageMetadata(
        turn_id=turn_id,
        client_turn_id=client_turn_id,
    )
    await canvas.append_canvas_message(
        episode_id=episode_id,
        session_id=session_id,
        user_id=user_id,
        role=ChatMessageRole.USER,
        content=content,
        metadata=meta.model_dump(mode="json", exclude_none=True),
    )
    return True


async def persist_canvas_assistant_message(
    *,
    episode_id: int,
    session_id: int,
    user_id: int,
    content: str,
    turn_id: str,
    tool_calls_count: int,
    client_turn_id: str | None = None,
) -> None:
    """持久化助手最终文本消息"""
    meta = CanvasMessageMetadata(
        turn_id=turn_id,
        client_turn_id=client_turn_id,
        tool_calls_count=tool_calls_count,
    )
    await get_canvas_port().append_canvas_message(
        episode_id=episode_id,
        session_id=session_id,
        user_id=user_id,
        role=ChatMessageRole.ASSISTANT,
        content=content,
        metadata=meta.model_dump(mode="json", exclude_none=True),
    )


async def persist_canvas_tool_step(
    *,
    episode_id: int,
    session_id: int,
    user_id: int,
    turn_id: str,
    step: CanvasToolStepMetadata,
) -> None:
    """持久化单个工具步骤, 供前端工具链时间线展示"""
    await get_canvas_port().append_canvas_message(
        episode_id=episode_id,
        session_id=session_id,
        user_id=user_id,
        role=ChatMessageRole.ASSISTANT,
        content="",
        metadata=CanvasMessageMetadata(
            turn_id=turn_id,
            phase="tool_step",
            tool_step=step,
        ).model_dump(mode="json", exclude_none=True),
    )
