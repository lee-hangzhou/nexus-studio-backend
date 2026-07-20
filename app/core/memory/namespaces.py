"""Shared LangGraph store namespace templates for Chat and Canvas memories."""

from __future__ import annotations

CHAT_USER_MEMORY_NAMESPACE = ("chat_memories", "{langgraph_user_id}")
CHAT_CONVERSATION_MEMORY_NAMESPACE = (
    "chat_memories",
    "{langgraph_user_id}",
    "{conversation_id}",
)

CANVAS_PROJECT_MEMORY_NAMESPACE = (
    "canvas_memories",
    "{langgraph_user_id}",
    "{project_id}",
)
CANVAS_USER_MEMORY_NAMESPACE = ("canvas_memories", "{langgraph_user_id}")
