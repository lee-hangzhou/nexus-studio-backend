from langchain_core.tools import StructuredTool

from app.canvas.tools.assets import build_get_asset_tool, build_list_assets_tool
from app.canvas.tools.guard import guard_canvas_tool
from app.canvas.tools.canvas_read import build_query_canvas_nodes_tool
from app.canvas.tools.canvas_write import build_apply_canvas_patch_tool
from app.canvas.tools.generate_models import build_list_generate_models_tool
from app.canvas.tools.generation import build_submit_node_generation_tool
from app.canvas.tools.inputs import build_resolve_node_inputs_tool
from app.canvas.tools.list_generations import build_list_node_generations_tool
from app.canvas.tools.memory_tools import build_canvas_memory_tools
from app.core.turn.tool_loop_guard import TurnToolLoopGuard


def build_canvas_tools(
    project_id: int,
    user_id: int,
    *,
    turn_id_holder: dict[str, str | None] | None = None,
    loop_guard: TurnToolLoopGuard | None = None,
) -> list[StructuredTool]:
    """按固定顺序注册画布 Agent 全部工具"""
    holder = turn_id_holder if turn_id_holder is not None else {"turn_id": None}
    tools: list[StructuredTool] = [
        build_query_canvas_nodes_tool(project_id),
        build_apply_canvas_patch_tool(project_id, user_id=user_id, turn_id_holder=holder),
        build_list_generate_models_tool(loop_guard=loop_guard),
        build_submit_node_generation_tool(project_id, user_id),
        build_list_node_generations_tool(project_id, user_id),
        build_resolve_node_inputs_tool(project_id),
        build_list_assets_tool(user_id),
        build_get_asset_tool(user_id),
    ]
    tools.extend(build_canvas_memory_tools(loop_guard=loop_guard))
    return [guard_canvas_tool(tool) for tool in tools]
