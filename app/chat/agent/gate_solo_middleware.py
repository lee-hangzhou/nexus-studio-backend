"""Atomic gate solo batch guard — runs in after_model before ToolNode."""

from __future__ import annotations

from typing import Any

from langchain.agents.middleware.types import AgentMiddleware, hook_config
from langgraph.types import Overwrite

from app.chat.agent.gate_solo import (
    batch_violation,
    last_ai_message_with_tool_calls,
    synthesize_batch_error_tool_messages,
)
from app.core.logger import logger


class GateSoloBatchMiddleware(AgentMiddleware):
    name = "gate_solo_batch"

    @hook_config(can_jump_to=["model"])
    def after_model(self, state: dict[str, Any], runtime: Any) -> dict[str, Any] | None:
        messages = list(state.get("messages") or [])
        found = last_ai_message_with_tool_calls(messages)
        if found is None:
            return None
        _idx, ai_msg = found
        tool_calls = list(ai_msg.tool_calls or [])
        if not batch_violation(tool_calls):
            return None

        tool_names = [str(c.get("name") or "") for c in tool_calls]
        logger.info(
            "gate_rejected",
            reason="batch_isolation",
            tool_names=tool_names,
            tool_calls_count=len(tool_calls),
        )

        error_messages = synthesize_batch_error_tool_messages(tool_calls)
        if not error_messages:
            return {"jump_to": "model"}

        return {
            "messages": Overwrite(messages + error_messages),
            "jump_to": "model",
        }

    async def aafter_model(self, state: dict[str, Any], runtime: Any) -> dict[str, Any] | None:
        return self.after_model(state, runtime)
