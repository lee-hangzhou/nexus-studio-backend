from __future__ import annotations

# 末段 records 终止 Postgres LIKE 前缀，避免 id "1" 命中 "10"/"100"
CHAT_USER_MEMORY_NAMESPACE = ("chat", "memory", "user", "{langgraph_user_id}", "records")
CANVAS_USER_MEMORY_NAMESPACE = ("canvas", "memory", "user", "{langgraph_user_id}", "records")
CANVAS_PROJECT_MEMORY_NAMESPACE = (
    "canvas",
    "memory",
    "project",
    "{langgraph_user_id}",
    "{project_id}",
    "records",
)
