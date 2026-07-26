from __future__ import annotations

from langgraph.store.base import BaseStore

from app.agent.runtime.runtime import runtime_resources


def get_memory_store() -> BaseStore | None:
    """返回进程级 memory store；未启用时为 None"""
    return runtime_resources.memory_store


def set_memory_store(store: BaseStore | None) -> None:
    """注入或清空进程级 memory store"""
    runtime_resources.memory_store = store
