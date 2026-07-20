from __future__ import annotations

from langchain_core.runnables import RunnableConfig

from app.core.agent_limits import langgraph_recursion_limit
from app.core.config import settings
from app.core.memory.namespaces import (
    CANVAS_PROJECT_MEMORY_NAMESPACE,
    CANVAS_USER_MEMORY_NAMESPACE,
)

PROJECT_MEMORY_NAMESPACE = CANVAS_PROJECT_MEMORY_NAMESPACE
USER_MEMORY_NAMESPACE = CANVAS_USER_MEMORY_NAMESPACE


def canvas_runnable_config(
    *,
    thread_id: str,
    user_id: int,
    project_id: int,
    mode: str = "auto",
) -> RunnableConfig:
    """生成本轮 Agent RunnableConfig, 含 thread_id 与 memory namespace 占位"""
    return {
        "configurable": {
            "thread_id": thread_id,
            "langgraph_user_id": str(user_id),
            "project_id": str(project_id),
            "mode": mode,
        },
        "recursion_limit": langgraph_recursion_limit(
            settings.CANVAS_MAX_ITERATIONS
        ),
    }
