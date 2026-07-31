"""非法 tool 参数 after_model 中间件: 注回失败 ToolMessage 并跳回 model

用户确认路径 (2026-07-31): 非法 tool args 不得打穿 turn; 优先同轮注回消息态让模型改,
graph 仍无答时才允许 empty recovery 兜底
"""

from __future__ import annotations

from typing import Any

from langchain.agents.middleware.types import AgentMiddleware, hook_config
from langchain_core.messages import AIMessage
from langgraph.types import Overwrite

from app.agent.chat.agent.invalid_tool_args import (
    INVALID_TOOL_CALLS_KWARG,
    read_invalid_tool_calls_kwargs,
    stub_tool_calls_for_invalid,
    synthesize_invalid_tool_arg_messages,
)
from app.server.infra.logger import logger


class InvalidToolArgsMiddleware(AgentMiddleware):
    """当本步只有非法 tool args 时, 注入错误 ToolMessage 并 jump_to model"""

    name = "invalid_tool_args"

    @hook_config(can_jump_to=["model"])
    def after_model(self, state: dict[str, Any], runtime: Any) -> dict[str, Any] | None:
        """把非法 tool args 注回消息态, 让模型同轮看见失败后再改"""
        messages = list(state.get("messages") or [])
        if not messages:
            return None
        last = messages[-1]
        if not isinstance(last, AIMessage):
            return None
        if last.tool_calls:
            # 混有可执行 tool_calls 时交给 ToolNode; 避免 stub 被重复执行
            return None
        invalid = read_invalid_tool_calls_kwargs(last)
        if not invalid:
            return None

        error_messages = synthesize_invalid_tool_arg_messages(invalid)
        if not error_messages:
            logger.error(
                "chat.invalid_tool_args.synthesize_empty",
                tool_calls_count=len(invalid),
            )
            return None

        stubs = stub_tool_calls_for_invalid(invalid)
        rewritten = AIMessage(
            content=last.content,
            tool_calls=stubs,
            additional_kwargs={
                k: v
                for k, v in last.additional_kwargs.items()
                if k != INVALID_TOOL_CALLS_KWARG
            },
            response_metadata=dict(last.response_metadata or {}),
            id=last.id,
            name=last.name,
        )
        logger.warning(
            "chat.invalid_tool_args.injected",
            tool_names=[c.name for c in invalid],
            tool_calls_count=len(invalid),
        )
        return {
            "messages": Overwrite(messages[:-1] + [rewritten] + error_messages),
            "jump_to": "model",
        }

    async def aafter_model(self, state: dict[str, Any], runtime: Any) -> dict[str, Any] | None:
        """异步入口, 委托同步 after_model"""
        return self.after_model(state, runtime)
