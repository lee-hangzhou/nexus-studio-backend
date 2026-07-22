"""Browser script execution tool."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated

from langchain.tools import InjectedToolArg
from langchain_core.runnables import RunnableConfig

from app.agent.chat.browser import runtime as browser_runtime
from app.agent.chat.tools.result import TURN_INTERRUPTED, ToolResult

EXEC_CALL_COUNT = 0


def reset_exec_call_count() -> None:
    global EXEC_CALL_COUNT
    EXEC_CALL_COUNT = 0


def _conversation_id_from_config(config: RunnableConfig | None) -> int:
    if not config:
        raise ValueError("missing runnable config")
    raw = (config.get("configurable") or {}).get("conversation_id")
    if raw is None:
        raise ValueError("missing conversation_id in config")
    return int(raw)


async def browser_exec_script(
    code: str,
    *,
    config: Annotated[RunnableConfig, InjectedToolArg] = None,
    cancel_event: asyncio.Event | None = None,
) -> str:
    """Run Python in the browser session with `page`, `context`, `Path` in scope."""
    global EXEC_CALL_COUNT
    EXEC_CALL_COUNT += 1

    if cancel_event is not None and cancel_event.is_set():
        return ToolResult.fail(TURN_INTERRUPTED, detail="turn cancelled").to_tool_message()

    conversation_id = _conversation_id_from_config(config)
    workspace = Path((config.get("configurable") or {}).get("workspace") or "/tmp")
    result = await browser_runtime.exec_user_script(
        conversation_id=conversation_id,
        workspace=workspace,
        code=code,
        cancel_event=cancel_event,
    )
    return result.to_tool_message()
