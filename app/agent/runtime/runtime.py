from __future__ import annotations

from dataclasses import dataclass

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.store.base import BaseStore


@dataclass
class RuntimeResources:
    chat_checkpointer: BaseCheckpointSaver | None = None
    canvas_memory_store: BaseStore | None = None


runtime_resources = RuntimeResources()
