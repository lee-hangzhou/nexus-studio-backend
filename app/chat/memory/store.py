from __future__ import annotations

from langchain_core.runnables import RunnableConfig

from app.chat.constants import CHAT_CHECKPOINT_THREAD_PREFIX
from app.core.agent_limits import langgraph_recursion_limit
from app.core.config import settings


def chat_runnable_config(
    *,
    user_id: int,
    conversation_id: int,
    workspace: str | None = None,
    turn_id: str | None = None,
) -> RunnableConfig:
    """生成本轮 Chat Agent RunnableConfig，含 thread_id 与 memory namespace 占位。"""
    configurable: dict[str, str] = {
        "thread_id": f"{CHAT_CHECKPOINT_THREAD_PREFIX}-{conversation_id}",
        "langgraph_user_id": str(user_id),
        "user_id": str(user_id),
        "conversation_id": str(conversation_id),
    }
    if workspace is not None:
        configurable["workspace"] = workspace
    if turn_id is not None:
        configurable["turn_id"] = turn_id
    return {
        "configurable": configurable,
        "recursion_limit": langgraph_recursion_limit(settings.CHAT_MAX_ITERATIONS),
    }
