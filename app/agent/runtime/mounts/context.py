"""Mount turn context — shared minimum interface for AgentMountSpec callables."""

from __future__ import annotations

import asyncio
from typing import Protocol

from langgraph.checkpoint.base import BaseCheckpointSaver


class MountTurnContext(Protocol):
    @property
    def user_id(self) -> int: ...

    @property
    def conversation_id(self) -> int | str: ...

    @property
    def turn_id(self) -> str: ...

    @property
    def cancel_event(self) -> asyncio.Event: ...

    @property
    def checkpointer(self) -> BaseCheckpointSaver: ...
