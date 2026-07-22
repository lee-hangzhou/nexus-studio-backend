from __future__ import annotations

from app.contracts.metadata import CanvasMessageMetadata, CanvasToolStepMetadata
from app.server.chat.domain.enums import ChatMessageRole
from app.server.exceptions.base import AppError
from app.server.exceptions.codes import ErrorCode
from app.server.canvas.persistence.messages import CanvasMessages


async def canvas_turn_already_completed(project_id: int, client_turn_id: str) -> bool:
    """判断 client_turn_id 是否已有助手终态消息"""
    assistant = await CanvasMessages.filter(
        project_id=project_id,
        role=int(ChatMessageRole.ASSISTANT),
        metadata__contains={"client_turn_id": client_turn_id},
    ).first()
    return assistant is not None


async def persist_canvas_user_message(
    *,
    project_id: int,
    user_id: int,
    content: str,
    client_turn_id: str | None,
    turn_id: str,
) -> tuple[CanvasMessages, bool]:
    """持久化用户消息, 返回 row 与是否新建, 重复 client_turn_id 已完成则抛错"""
    if client_turn_id:
        if await canvas_turn_already_completed(project_id, client_turn_id):
            raise AppError(
                ErrorCode.CANVAS_DUPLICATE_TURN,
                "canvas turn already completed",
                details={"client_turn_id": client_turn_id},
            )
        existing = await CanvasMessages.filter(
            project_id=project_id,
            role=int(ChatMessageRole.USER),
            metadata__contains={"client_turn_id": client_turn_id},
        ).first()
        if existing is not None:
            return existing, False
    meta = CanvasMessageMetadata(
        turn_id=turn_id,
        client_turn_id=client_turn_id,
    )
    row = await CanvasMessages.create(
        project_id=project_id,
        user_id=user_id,
        role=int(ChatMessageRole.USER),
        content=content,
        metadata=meta.model_dump(mode="json", exclude_none=True),
    )
    return row, True


async def persist_canvas_assistant_message(
    *,
    project_id: int,
    user_id: int,
    content: str,
    turn_id: str,
    tool_calls_count: int,
    client_turn_id: str | None = None,
) -> CanvasMessages:
    """持久化助手最终文本消息"""
    meta = CanvasMessageMetadata(
        turn_id=turn_id,
        client_turn_id=client_turn_id,
        tool_calls_count=tool_calls_count,
    )
    return await CanvasMessages.create(
        project_id=project_id,
        user_id=user_id,
        role=int(ChatMessageRole.ASSISTANT),
        content=content,
        metadata=meta.model_dump(mode="json", exclude_none=True),
    )


async def persist_canvas_tool_step(
    *,
    project_id: int,
    user_id: int,
    turn_id: str,
    step: CanvasToolStepMetadata,
) -> None:
    """持久化单个工具步骤, 供前端工具链时间线展示"""
    await CanvasMessages.create(
        project_id=project_id,
        user_id=user_id,
        role=int(ChatMessageRole.ASSISTANT),
        content="",
        metadata=CanvasMessageMetadata(
            turn_id=turn_id,
            phase="tool_step",
            tool_step=step,
        ).model_dump(mode="json", exclude_none=True),
    )
