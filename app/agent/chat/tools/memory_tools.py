from __future__ import annotations

import json
from typing import Annotated, Any

from langchain.tools import InjectedToolArg
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import StructuredTool
from langmem import create_manage_memory_tool, create_search_memory_tool
from langmem import utils as langmem_utils

from app.agent.chat.memory.schemas import ChatMemory
from app.agent.chat.tools.definitions import MEMORY_TOOL_DESCRIPTIONS
from app.agent.chat.tools.result import TOOL_LOOP_EXHAUSTED, ToolResult
from app.agent.runtime.memory.namespaces import (
    CHAT_CONVERSATION_MEMORY_NAMESPACE,
    CHAT_USER_MEMORY_NAMESPACE,
)
from app.agent.runtime.memory.tool_errors import (
    format_memory_success,
    is_memory_unavailable_error,
    memory_tool_fail,
    run_memory_tool_call,
)
from app.agent.runtime.memory_store import get_memory_store
from app.agent.runtime.turn.tool_loop_guard import (
    LoopBlockInfo,
    TurnToolLoopGuard,
    ToolLoopTrack,
    TOOL_LOOP_POLICIES,
    is_json_list_output_empty,
    loop_args_from_kwargs,
)

_LIST_DEFAULT_LIMIT = 100


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

    async def wrapped(**kwargs: Any) -> str:
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
            if loop_guard is not None:
                policy = TOOL_LOOP_POLICIES.get(tool.name)
                track_loop = policy is not None and policy.track == ToolLoopTrack.EMPTY_STREAK
                if track_loop:
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


def _serialize_list_items(items: list[Any]) -> str:
    rows: list[dict[str, Any]] = []
    for item in items:
        value = getattr(item, "value", None) or {}
        content = value.get("content", value)
        rows.append(
            {
                "id": getattr(item, "key", None),
                "content": content,
            }
        )
    return json.dumps(rows, ensure_ascii=False)


def _build_list_tool(
    *,
    name: str,
    namespace_template: tuple[str, ...],
    description: str,
    store: Any,
    loop_guard: TurnToolLoopGuard | None,
) -> StructuredTool:
    namespacer = langmem_utils.NamespaceTemplate(namespace_template)

    async def alist_memories(
        limit: int = _LIST_DEFAULT_LIMIT,
        offset: int = 0,
        config: Annotated[RunnableConfig, InjectedToolArg] = None,
    ) -> str:
        args = {"limit": limit, "offset": offset}
        if loop_guard is not None:
            blocked = loop_guard.pre_check(name, args=args)
            if blocked is not None:
                return _loop_exhausted_message(blocked)
        try:
            namespace = namespacer(config)

            async def _search() -> list[Any]:
                return await store.asearch(
                    namespace,
                    query=None,
                    limit=limit,
                    offset=offset,
                )

            items = await run_memory_tool_call(_search, tool_name=name)
            is_empty = len(items) == 0
            result = ToolResult.ok(_serialize_list_items(items))
            if loop_guard is not None:
                loop_guard.record(name, args=args, success=result.success, is_empty=is_empty)
            return result.to_tool_message()
        except Exception as exc:
            if is_memory_unavailable_error(exc):
                configurable = (config or {}).get("configurable") or {}
                return memory_tool_fail(
                    tool_name=name,
                    exc=exc,
                    user_id=configurable.get("langgraph_user_id"),
                    conversation_id=configurable.get("conversation_id"),
                ).to_tool_message()
            raise

    return StructuredTool.from_function(
        coroutine=alist_memories,
        name=name,
        description=description,
    )


def build_chat_memory_tools(
    loop_guard: TurnToolLoopGuard | None = None,
) -> list[StructuredTool]:
    """构建 Chat 六记忆工具；store 未就绪时返回空列表。"""
    store = get_memory_store()
    if store is None:
        return []

    manage_user = create_manage_memory_tool(
        namespace=CHAT_USER_MEMORY_NAMESPACE,
        schema=ChatMemory,
        store=store,
        name="manage_user_memory",
        instructions="",
    )
    recall_user = create_search_memory_tool(
        namespace=CHAT_USER_MEMORY_NAMESPACE,
        store=store,
        name="recall_user_memory",
        instructions="",
    )
    manage_conversation = create_manage_memory_tool(
        namespace=CHAT_CONVERSATION_MEMORY_NAMESPACE,
        schema=ChatMemory,
        store=store,
        name="manage_conversation_memory",
        instructions="",
    )
    recall_conversation = create_search_memory_tool(
        namespace=CHAT_CONVERSATION_MEMORY_NAMESPACE,
        store=store,
        name="recall_conversation_memory",
        instructions="",
    )
    list_user = _build_list_tool(
        name="list_user_memories",
        namespace_template=CHAT_USER_MEMORY_NAMESPACE,
        description=MEMORY_TOOL_DESCRIPTIONS["list_user_memories"],
        store=store,
        loop_guard=loop_guard,
    )
    list_conversation = _build_list_tool(
        name="list_conversation_memories",
        namespace_template=CHAT_CONVERSATION_MEMORY_NAMESPACE,
        description=MEMORY_TOOL_DESCRIPTIONS["list_conversation_memories"],
        store=store,
        loop_guard=loop_guard,
    )

    return [
        _wrap_langmem_tool(
            manage_user,
            description=MEMORY_TOOL_DESCRIPTIONS["manage_user_memory"],
            loop_guard=loop_guard,
            track_empty=False,
        ),
        _wrap_langmem_tool(
            recall_user,
            description=MEMORY_TOOL_DESCRIPTIONS["recall_user_memory"],
            loop_guard=loop_guard,
            track_empty=True,
        ),
        list_user,
        _wrap_langmem_tool(
            manage_conversation,
            description=MEMORY_TOOL_DESCRIPTIONS["manage_conversation_memory"],
            loop_guard=loop_guard,
            track_empty=False,
        ),
        _wrap_langmem_tool(
            recall_conversation,
            description=MEMORY_TOOL_DESCRIPTIONS["recall_conversation_memory"],
            loop_guard=loop_guard,
            track_empty=True,
        ),
        list_conversation,
    ]
