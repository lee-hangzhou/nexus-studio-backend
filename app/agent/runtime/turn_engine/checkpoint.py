"""Shared checkpoint repair invocation for turn start / cleanup."""

from __future__ import annotations

from typing import Any, Protocol

from langchain_core.runnables import RunnableConfig
from langgraph.graph.state import CompiledStateGraph

from app.server.infra.logger import logger


class CheckpointRepairFn(Protocol):
    async def __call__(
        self,
        agent: CompiledStateGraph,
        runnable_config: RunnableConfig,
        /,
        **kwargs: Any,
    ) -> None: ...


async def run_checkpoint_repair(
    repair: CheckpointRepairFn | None,
    *,
    agent: CompiledStateGraph,
    runnable_config: RunnableConfig,
    thread_id: str,
    turn_id: str,
    reason: str,
    **extra: Any,
) -> None:
    """Invoke surface repair with a stable kwargs contract; swallow + log failures."""
    if repair is None:
        return
    try:
        await repair(
            agent,
            runnable_config,
            thread_id=thread_id,
            turn_id=turn_id,
            reason=reason,
            **extra,
        )
    except Exception as exc:
        logger.exception(
            "turn.checkpoint_repair_failed",
            turn_id=turn_id,
            reason=reason,
            error=str(exc),
        )
