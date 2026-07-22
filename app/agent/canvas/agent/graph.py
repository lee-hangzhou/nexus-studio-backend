from __future__ import annotations

from langchain.agents import create_agent
from langchain.agents.middleware import HumanInTheLoopMiddleware, SummarizationMiddleware
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph
from langgraph.store.base import BaseStore

from app.agent.chat.llm.gateway_chat_model import GatewayChatModel
from app.server.infra.config import settings


def build_canvas_agent_graph(
    llm: GatewayChatModel,
    *,
    tools: list[BaseTool],
    checkpointer: BaseCheckpointSaver,
    store: BaseStore | None,
    system_prompt: str,
    summarization_llm: GatewayChatModel | None = None,
    mode: str = "auto",
) -> CompiledStateGraph:
    """创建画布 Agent 图, 含摘要中间件与手动确认中间件"""
    middleware = [
        SummarizationMiddleware(
            model=summarization_llm or llm,
            trigger=("tokens", settings.canvas_max_tokens_before_summary),
            keep=("tokens", settings.CANVAS_SUMMARY_KEEP_TOKENS),
            trim_tokens_to_summarize=settings.CANVAS_SUMMARY_TRIM_TO_SUMMARIZE,
        )
    ]
    if mode == "manual":
        # 手动模式只拦截写工具确认, 不改变 Agent 决策循环
        middleware.append(
            HumanInTheLoopMiddleware(
                interrupt_on={
                    name: {"allowed_decisions": ["approve", "reject"]}
                    for name in settings.canvas_manual_confirm_tools
                }
            )
        )

    # create_agent 返回已编译图, checkpointer 与 store 交给 LangGraph
    return create_agent(
        llm,
        tools=tools or None,
        system_prompt=system_prompt,
        middleware=middleware,
        checkpointer=checkpointer,
        store=store,
        debug=False,
        name="canvas_agent",
    )
