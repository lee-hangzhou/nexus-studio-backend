from langchain_core.tools import StructuredTool

from app.agent.canvas.tools.assets import build_get_asset_tool, build_list_assets_tool
from app.agent.canvas.tools.guard import guard_canvas_tool
from app.agent.canvas.tools.canvas_read import build_query_canvas_nodes_tool
from app.agent.canvas.tools.canvas_write import build_apply_canvas_patch_tool
from app.agent.canvas.tools.generate_models import build_list_generate_models_tool
from app.agent.canvas.tools.generation import build_submit_node_generation_tool
from app.agent.canvas.tools.inputs import build_resolve_node_inputs_tool
from app.agent.canvas.tools.list_generations import build_list_node_generations_tool
from app.agent.canvas.tools.memory_tools import build_canvas_memory_tools
from app.agent.runtime.tools.inspect_turn_media import build_inspect_turn_media_tool
from app.agent.runtime.tools.user_skill_tools import build_write_user_skill_file_tool
from app.agent.runtime.turn.tool_loop_guard import TurnToolLoopGuard
from app.contracts.turn_content import TurnMediaType
from app.server.skills.domain.enums import SkillSurface


def build_canvas_tools(
    *,
    project_id: int,
    episode_id: int,
    user_id: int,
    turn_id_holder: dict[str, str | None],
    tool_asset_ids: frozenset[int],
    asset_media_types: dict[int, TurnMediaType],
    loop_guard: TurnToolLoopGuard | None = None,
    surface: str = SkillSurface.CANVAS,
) -> list[StructuredTool]:
    """按固定顺序注册画布 Agent 全部工具"""
    tools: list[StructuredTool] = [
        build_query_canvas_nodes_tool(project_id=project_id, episode_id=episode_id, user_id=user_id),
        build_apply_canvas_patch_tool(
            project_id=project_id,
            episode_id=episode_id,
            user_id=user_id,
            turn_id_holder=turn_id_holder,
        ),
        build_list_generate_models_tool(loop_guard=loop_guard),
        build_submit_node_generation_tool(project_id=project_id, episode_id=episode_id, user_id=user_id),
        build_list_node_generations_tool(episode_id, user_id),
        build_resolve_node_inputs_tool(episode_id),
        build_list_assets_tool(user_id),
        build_get_asset_tool(user_id),
    ]
    if tool_asset_ids:
        tools.append(
            build_inspect_turn_media_tool(
                user_id=user_id,
                allowed_asset_ids=tool_asset_ids,
                asset_media_types=asset_media_types,
            )
        )
    tools.extend(build_canvas_memory_tools(loop_guard=loop_guard))
    tools.append(
        build_write_user_skill_file_tool(
            surface=surface,
            user_id=user_id,
        )
    )
    return [guard_canvas_tool(tool) for tool in tools]


def build_canvas_inspect_only_tools(
    *,
    user_id: int,
    tool_asset_ids: frozenset[int],
    asset_media_types: dict[int, TurnMediaType],
) -> list[StructuredTool]:
    """enable_tools=false 但仍有视觉引用时，仅挂载 inspect_turn_media"""
    if not tool_asset_ids:
        return []
    return [
        guard_canvas_tool(
            build_inspect_turn_media_tool(
                user_id=user_id,
                allowed_asset_ids=tool_asset_ids,
                asset_media_types=asset_media_types,
            )
        )
    ]
