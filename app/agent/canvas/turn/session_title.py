from __future__ import annotations

import asyncio

from app.agent.chat.conversation_title import (
    TitleApplyResult,
    is_placeholder_title,
    propose_sidebar_title_via_llm,
)
from app.agent.runtime.ports import get_canvas_port
from app.server.canvas.domain.constants import CANVAS_DEFAULT_SESSION_TITLE
from app.server.infra.logger import logger


def is_canvas_auto_title_eligible(title: str) -> bool:
    """是否仍为可被 LLM 覆盖的占位/临时标题"""
    if is_placeholder_title(title, placeholder=CANVAS_DEFAULT_SESSION_TITLE):
        return True
    return title.startswith(f"{CANVAS_DEFAULT_SESSION_TITLE} ·")


async def generate_canvas_session_title_via_llm(
    *,
    episode_id: int,
    session_id: int,
    user_id: int,
    user_content: str,
    model_key: str,
) -> TitleApplyResult:
    """首轮且仍为占位标题时生成并写回画布 session 标题"""
    canvas = get_canvas_port()
    current = await canvas.get_session_title(
        episode_id=episode_id,
        session_id=session_id,
        user_id=user_id,
    )
    if current is None or not is_canvas_auto_title_eligible(current):
        logger.info(
            "canvas.session_title.llm_skipped",
            session_id=session_id,
            reason="not_placeholder",
        )
        return TitleApplyResult(applied=False)

    new_title = await propose_sidebar_title_via_llm(
        user_content=user_content,
        attachment_filenames_list=[],
        model_key=model_key,
        placeholder_title=CANVAS_DEFAULT_SESSION_TITLE,
        log_event_prefix="canvas.session_title",
        log_context={"session_id": session_id, "episode_id": episode_id},
    )
    if new_title is None:
        return TitleApplyResult(applied=False)

    applied, updated_at = await canvas.apply_session_title_if_unchanged(
        episode_id=episode_id,
        session_id=session_id,
        user_id=user_id,
        expected_title=current,
        new_title=new_title,
    )
    if not applied:
        logger.info(
            "canvas.session_title.llm_skipped",
            session_id=session_id,
            reason="title_changed",
        )
        return TitleApplyResult(applied=False)

    await canvas.publish_episode_session_title(
        episode_id,
        session_id=session_id,
        title=new_title,
        updated_at=updated_at or "",
    )
    logger.info(
        "canvas.session_title.llm_ok",
        session_id=session_id,
        episode_id=episode_id,
        title=new_title,
    )
    return TitleApplyResult(applied=True, title=new_title, updated_at=updated_at)


def schedule_canvas_session_title(
    *,
    episode_id: int,
    session_id: int,
    user_id: int,
    user_content: str,
    model_key: str,
) -> None:
    """后台调度画布 session 标题生成, 不阻塞 turn"""
    asyncio.create_task(
        _background_canvas_session_title(
            episode_id=episode_id,
            session_id=session_id,
            user_id=user_id,
            user_content=user_content,
            model_key=model_key,
        )
    )


async def _background_canvas_session_title(
    *,
    episode_id: int,
    session_id: int,
    user_id: int,
    user_content: str,
    model_key: str,
) -> None:
    """执行后台标题生成并吞掉未预期异常"""
    try:
        await generate_canvas_session_title_via_llm(
            episode_id=episode_id,
            session_id=session_id,
            user_id=user_id,
            user_content=user_content,
            model_key=model_key,
        )
    except Exception:
        logger.exception(
            "canvas.session_title.background_failed",
            session_id=session_id,
            episode_id=episode_id,
        )
