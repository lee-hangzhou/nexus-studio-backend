from __future__ import annotations

from langchain_core.tools import StructuredTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph

from app.agent.canvas.agent.graph import build_canvas_agent_graph
from app.agent.canvas.prompt.composer import compose_canvas_system_prompt
from app.agent.canvas.tools.build import build_canvas_inspect_only_tools, build_canvas_tools
from app.agent.chat.llm.gateway_chat_model import GatewayChatModel
from app.agent.runtime.memory.inject import MemoryInjectionRequest, build_memory_injection
from app.agent.runtime.memory_store import get_memory_store
from app.agent.runtime.skills.assembler import build_turn_skill_library
from app.agent.runtime.turn.tool_loop_guard import TurnToolLoopGuard
from app.contracts.turn_content import TurnMediaType, TurnReferenceIndex
from app.server.ports.product import SelectedSkillDTO
from app.server.skills.domain.enums import SkillSurface

_MEMORY_TOOL_NAMES = frozenset(
    {
        "manage_user_memory",
        "recall_user_memory",
        "manage_project_memory",
        "recall_project_memory",
    }
)


async def build_canvas_agent(
    llm: GatewayChatModel,
    *,
    project_id: int,
    episode_id: int,
    user_id: int,
    checkpointer: BaseCheckpointSaver,
    enable_tools: bool,
    turn_id_holder: dict[str, str | None],
    reference_index: TurnReferenceIndex,
    tool_asset_ids: frozenset[int],
    asset_media_types: dict[int, TurnMediaType],
    mode: str = "auto",
    loop_guard: TurnToolLoopGuard | None = None,
    user_message: str = "",
    is_resume: bool = False,
    selected_skills: tuple[SelectedSkillDTO, ...] = (),
) -> tuple[CompiledStateGraph, str]:
    """组装 LLM, 工具, system prompt, checkpointer, store 为可运行图"""
    skill_library = build_turn_skill_library(reference_index=reference_index)
    if enable_tools:
        tools: list[StructuredTool] = build_canvas_tools(
            project_id=project_id,
            episode_id=episode_id,
            user_id=user_id,
            turn_id_holder=turn_id_holder,
            loop_guard=loop_guard,
            surface=SkillSurface.CANVAS,
            tool_asset_ids=tool_asset_ids,
            asset_media_types=asset_media_types,
            skill_library=skill_library,
        )
    else:
        tools = build_canvas_inspect_only_tools(
            user_id=user_id,
            tool_asset_ids=tool_asset_ids,
            asset_media_types=asset_media_types,
        )
    memory_tools_enabled = any(tool.name in _MEMORY_TOOL_NAMES for tool in tools)
    injection = await build_memory_injection(
        MemoryInjectionRequest(
            domain="canvas",
            user_id=user_id,
            project_id=project_id,
            user_message=user_message,
            is_resume=is_resume,
            memory_tools_enabled=memory_tools_enabled,
            store=get_memory_store(),
        )
    )
    system_prompt = await compose_canvas_system_prompt(
        project_id=project_id,
        episode_id=episode_id,
        user_id=user_id,
        selected_skills=selected_skills,
        is_resume=is_resume,
        memory_blocks_text=injection.memory_blocks_text,
        memory_ops_brief=injection.ops_brief_text,
        reference_index=reference_index,
        skill_library=skill_library,
    )
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
