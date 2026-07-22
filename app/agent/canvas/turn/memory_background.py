from __future__ import annotations

import asyncio

from langchain_core.messages import BaseMessage
from langmem import create_memory_store_manager

from app.agent.canvas.memory.model_resolve import resolve_memory_extract_model_key
from app.agent.canvas.memory.schemas import CanvasMemory
from app.agent.canvas.memory.store import PROJECT_MEMORY_NAMESPACE
from app.agent.chat.llm.gateway_chat_model import GatewayChatModel
from app.agent.chat.llm.registry import get_model_spec
from app.server.infra.config import settings
from app.server.infra.logger import logger
from app.agent.runtime.memory_store import get_canvas_memory_store


def schedule_canvas_memory_extract(
    *,
    messages: list[BaseMessage],
    user_id: int,
    project_id: int,
) -> None:
    """调度一次异步记忆抽取, 失败不影响主流程"""
    store = get_canvas_memory_store()
    if store is None or not settings.CANVAS_MEMORY_STORE_ENABLED:
        return

    async def _run() -> None:
        """后台调用 langmem, 把对话写入项目级长期记忆"""
        try:
            model_key = resolve_memory_extract_model_key()
            if not model_key:
                # 目录无可用抽取模型时只记日志
                logger.warning(
                    "canvas.memory.extract_skipped",
                    reason="no_chat_model_in_catalog",
                    preferred=settings.CANVAS_MEMORY_EXTRACT_MODEL,
                )
                return
            spec = get_model_spec(model_key)
            llm = GatewayChatModel(model_key=model_key, spec=spec)
            manager = create_memory_store_manager(
                llm,
                store=store,
                namespace=PROJECT_MEMORY_NAMESPACE,
                schemas=[CanvasMemory],
                enable_inserts=True,
                enable_updates=True,
                enable_deletes=True,
            )
            config = {
                "configurable": {
                    # langmem 用 configurable 定位用户与项目命名空间
                    "langgraph_user_id": str(user_id),
                    "project_id": str(project_id),
                }
            }
            await manager.ainvoke({"messages": messages}, config=config)
            logger.info(
                "canvas.memory.write",
                source="background_extract",
                namespace=str(PROJECT_MEMORY_NAMESPACE),
                project_id=project_id,
                user_id=user_id,
            )
        except Exception as exc:
            logger.exception("canvas.memory.background_failed", error=str(exc))

    asyncio.create_task(_run())
