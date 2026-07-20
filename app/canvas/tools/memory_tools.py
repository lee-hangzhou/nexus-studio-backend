from __future__ import annotations

from langchain_core.tools import StructuredTool
from langmem import create_manage_memory_tool, create_search_memory_tool

from app.canvas.memory.schemas import CanvasMemory
from app.canvas.memory.store import PROJECT_MEMORY_NAMESPACE, USER_MEMORY_NAMESPACE
from app.chat.tools.definitions import MEMORY_TOOL_DESCRIPTIONS
from app.chat.tools.result import TOOL_LOOP_EXHAUSTED, ToolResult
from app.core.memory.tool_errors import (
    format_memory_success,
    is_memory_unavailable_error,
    memory_tool_fail,
    run_memory_tool_call,
)
from app.core.memory_store import get_memory_store
from app.core.turn.tool_loop_guard import (
    LoopBlockInfo,
    TurnToolLoopGuard,
    is_json_list_output_empty,
    loop_args_from_kwargs,
)


def _loop_exhausted_message(blocked: LoopBlockInfo) -> str:
    return ToolResult.fail(TOOL_LOOP_EXHAUSTED, detail=blocked.detail()).to_tool_message()


def _wrap_langmem_tool(
    tool: StructuredTool,
    *,
    description: str,
    loop_guard: TurnToolLoopGuard | None,
    track_empty: bool,
) -> StructuredTool:
    coroutine = tool.coroutine
    if coroutine is None:
        raise ValueError(f"memory tool {tool.name} has no async coroutine")

    async def wrapped(**kwargs):
        args = loop_args_from_kwargs(kwargs)
        if loop_guard is not None:
            blocked = loop_guard.pre_check(tool.name, args=args)
            if blocked is not None:
                return _loop_exhausted_message(blocked)
        try:
            output = await run_memory_tool_call(
                lambda: coroutine(**kwargs),
                tool_name=tool.name,
            )
            result = format_memory_success(output)
            if loop_guard is not None and track_empty:
                loop_guard.record(
                    tool.name,
                    args=args,
                    success=result.success,
                    is_empty=is_json_list_output_empty(output),
                )
            return result.to_tool_message()
        except Exception as exc:
            if is_memory_unavailable_error(exc):
                return memory_tool_fail(tool_name=tool.name, exc=exc).to_tool_message()
            raise

    return StructuredTool(
        name=tool.name,
        description=description,
        coroutine=wrapped,
        args_schema=tool.args_schema,
    )


def build_canvas_memory_tools(
    loop_guard: TurnToolLoopGuard | None = None,
) -> list[StructuredTool]:
    """构建画布记忆工具：项目 manage/recall、用户 manage/recall。"""
    store = get_memory_store()
    if store is None:
        return []
    manage_project = create_manage_memory_tool(
        namespace=PROJECT_MEMORY_NAMESPACE,
        schema=CanvasMemory,
        store=store,
        name="manage_memory",
        instructions="",
    )
    manage_user = create_manage_memory_tool(
        namespace=USER_MEMORY_NAMESPACE,
        schema=CanvasMemory,
        store=store,
        name="manage_user_memory",
        instructions="",
    )
    recall_project = create_search_memory_tool(
        namespace=PROJECT_MEMORY_NAMESPACE,
        store=store,
        name="recall_project_memory",
        instructions="",
    )
    recall_user = create_search_memory_tool(
        namespace=USER_MEMORY_NAMESPACE,
        store=store,
        name="recall_user_memory",
        instructions="",
    )
    return [
        _wrap_langmem_tool(
            manage_project,
            description=manage_project.description,
            loop_guard=loop_guard,
            track_empty=False,
        ),
        _wrap_langmem_tool(
            manage_user,
            description=MEMORY_TOOL_DESCRIPTIONS["manage_user_memory"],
            loop_guard=loop_guard,
            track_empty=False,
        ),
        _wrap_langmem_tool(
            recall_project,
            description=recall_project.description,
            loop_guard=loop_guard,
            track_empty=True,
        ),
        _wrap_langmem_tool(
            recall_user,
            description=MEMORY_TOOL_DESCRIPTIONS["recall_user_memory"],
            loop_guard=loop_guard,
            track_empty=True,
        ),
    ]
