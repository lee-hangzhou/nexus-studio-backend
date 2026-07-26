from __future__ import annotations

from langchain_core.runnables import RunnableConfig

from app.agent.runtime.agent_limits import langgraph_recursion_limit
from app.agent.runtime.memory.namespaces import (
    CANVAS_PROJECT_MEMORY_NAMESPACE,
    CANVAS_USER_MEMORY_NAMESPACE,
)
from app.server.infra.config import settings

PROJECT_MEMORY_NAMESPACE = CANVAS_PROJECT_MEMORY_NAMESPACE
USER_MEMORY_NAMESPACE = CANVAS_USER_MEMORY_NAMESPACE


def canvas_runnable_config(
    *,
    thread_id: str,
    user_id: int,
    project_id: int,
    episode_id: int,
    mode: str = "manual",
    chat_model_id: str | None = None,
) -> RunnableConfig:
    """构建画布 Agent 的 LangGraph 运行配置"""
    configurable = {
        "thread_id": thread_id,
        "langgraph_user_id": str(user_id),
        "project_id": str(project_id),
        "episode_id": str(episode_id),
        "mode": mode,
    }
    if chat_model_id is not None:
        configurable["chat_model_id"] = chat_model_id
    return {
        "configurable": configurable,
        "recursion_limit": langgraph_recursion_limit(settings.CANVAS_MAX_ITERATIONS),
    }
