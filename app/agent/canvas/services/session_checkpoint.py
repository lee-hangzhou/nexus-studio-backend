from __future__ import annotations

from app.agent.runtime.checkpointer import get_chat_checkpointer
from app.server.canvas.domain.constants import CANVAS_CHECKPOINT_THREAD_PREFIX
from app.server.infra.config import settings
from app.server.infra.logger import logger


def canvas_session_checkpoint_thread_id(episode_id: int, session_id: int) -> str:
    """拼画布 session 的 checkpoint thread id"""
    prefix = settings.CANVAS_CHECKPOINT_THREAD_PREFIX or CANVAS_CHECKPOINT_THREAD_PREFIX
    return f"{prefix}:{episode_id}:{session_id}"


async def delete_canvas_session_checkpoint(episode_id: int, session_id: int) -> None:
    """删除指定 session 的 LangGraph checkpoint 线程"""
    saver = get_chat_checkpointer()
    thread_id = canvas_session_checkpoint_thread_id(episode_id, session_id)
    await saver.adelete_thread(thread_id)
    logger.info(
        "canvas.session.checkpoint_deleted",
        episode_id=episode_id,
        session_id=session_id,
        thread_id=thread_id,
    )
