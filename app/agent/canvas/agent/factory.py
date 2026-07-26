from __future__ import annotations

from langchain_core.tools import StructuredTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph

from app.agent.canvas.agent.graph import build_canvas_agent_graph
from app.agent.canvas.prompt.composer import compose_canvas_system_prompt
from app.agent.canvas.tools.build import build_canvas_tools
from app.agent.chat.llm.gateway_chat_model import GatewayChatModel
from app.agent.runtime.memory_store import get_memory_store
from app.agent.runtime.turn.tool_loop_guard import TurnToolLoopGuard


async def build_canvas_agent(
    llm: GatewayChatModel,
    *,
    project_id: int,
    episode_id: int,
    user_id: int,
    checkpointer: BaseCheckpointSaver,
    enable_tools: bool,
    mode: str = "auto",
    turn_id: str | None = None,
    turn_id_holder: dict[str, str | None] | None = None,
    loop_guard: TurnToolLoopGuard | None = None,
) -> tuple[CompiledStateGraph, str]:
    """组装 LLM, 工具, system prompt, checkpointer, store 为可运行图"""
    system_prompt = await compose_canvas_system_prompt(project_id=project_id, episode_id=episode_id)
    # turn_id_holder 可变容器, SSE turn 创建后工具执行可读最新 turn_id
    turn_id_holder = turn_id_holder if turn_id_holder is not None else {"turn_id": turn_id}
    if turn_id and not turn_id_holder.get("turn_id"):
        turn_id_holder["turn_id"] = turn_id
    tools: list[StructuredTool] = (
        build_canvas_tools(
            project_id=project_id,
            episode_id=episode_id,
            user_id=user_id,
            turn_id_holder=turn_id_holder,
            loop_guard=loop_guard,
        )
        if enable_tools
        else []
    )
    # 记忆走 LangGraph store, namespace 与读写生命周期由框架管理
    store = get_memory_store()
    graph = build_canvas_agent_graph(
        llm,
        tools=tools,
        checkpointer=checkpointer,
        store=store,
        system_prompt=system_prompt,
        mode=mode,
    )
    return graph, system_prompt
