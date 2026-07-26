from __future__ import annotations

import json
import uuid
from typing import Any, Literal

from langchain_core.tools import StructuredTool
from langmem import create_manage_memory_tool, create_search_memory_tool
from langmem import utils as langmem_utils
from pydantic import BaseModel, Field

from app.agent.runtime.memory.envelope import (
    canonical_project_value,
    unwrap_store_content,
)
from app.agent.runtime.memory.instructions import (
    PROJECT_MANAGE_DESCRIPTION,
    PROJECT_RECALL_DESCRIPTION,
    PROJECT_RECALL_INSTRUCTIONS,
    USER_MANAGE_DESCRIPTION_CANVAS,
    USER_MANAGE_DESCRIPTION_CHAT,
    USER_MANAGE_INSTRUCTIONS_CANVAS,
    USER_MANAGE_INSTRUCTIONS_CHAT,
    USER_RECALL_DESCRIPTION,
    USER_RECALL_INSTRUCTIONS,
)
from app.agent.runtime.memory.registry import get_memory_domain
from app.agent.runtime.memory.schemas import ProjectFactMemory, UserMemory
from app.agent.runtime.memory.secrets import SecretScanHit, scan_text_for_secrets
from app.agent.runtime.memory.tool_errors import (
    format_memory_success,
    is_memory_unavailable_error,
    memory_tool_fail,
    run_memory_tool_call,
)
from app.agent.runtime.memory_store import get_memory_store
from app.agent.runtime.tools.result import MEMORY_SENSITIVE_REJECTED, ToolResult
from app.agent.runtime.turn.tool_loop_guard import (
    TOOL_LOOP_POLICIES,
    LoopBlockInfo,
    ToolLoopTrack,
    TurnToolLoopGuard,
    is_json_list_output_empty,
    loop_args_from_kwargs,
)
from app.server.infra.logger import logger


def _loop_exhausted_message(blocked: LoopBlockInfo) -> str:
    """将工具循环阻断格式化为 ToolResult 消息"""
    return ToolResult.fail("tool_loop_exhausted", detail=blocked.detail()).to_tool_message()


def _payload_text_for_scan(payload: BaseModel) -> str:
    """将 payload 字段拼成 detect-secrets 扫描文本"""
    data = payload.model_dump(mode="json")
    return "\n".join(str(v) for v in data.values() if v is not None and str(v).strip())


def _reject_sensitive(
    *,
    tool_name: str,
    domain: str,
    scope: str,
    hits: list[SecretScanHit],
    turn_id: str | None = None,
) -> str:
    """返回结构化凭证拒写结果；日志不含密文"""
    logger.info(
        "memory.sensitive_rejected",
        tool_name=tool_name,
        domain=domain,
        scope=scope,
        turn_id=turn_id,
        secret_types=[h.secret_type for h in hits],
        reason="detect_secrets",
    )
    return ToolResult.fail(
        MEMORY_SENSITIVE_REJECTED,
        detail="credential content rejected by detect-secrets",
    ).to_tool_message()


def _wrap_langmem_tool(
    tool: StructuredTool,
    *,
    description: str,
    loop_guard: TurnToolLoopGuard | None,
    track_empty: bool,
    schema: type[BaseModel] | None = None,
    scan_on_write: bool = False,
    domain: str = "",
    scope: str = "",
) -> StructuredTool:
    """包装 LangMem 工具：循环守卫、ToolResult、可选凭证扫描"""
    coroutine = tool.coroutine
    if coroutine is None:
        raise ValueError(f"memory tool {tool.name} has no async coroutine")

    async def wrapped(**kwargs: Any) -> str:
        """执行记忆工具并统一错误信封"""
        args = loop_args_from_kwargs(kwargs)
        if loop_guard is not None:
            blocked = loop_guard.pre_check(tool.name, args=args)
            if blocked is not None:
                return _loop_exhausted_message(blocked)
        try:
            action = kwargs.get("action") or "create"
            content = kwargs.get("content")
            if scan_on_write and action in ("create", "update") and content is not None:
                if isinstance(content, BaseModel):
                    payload = content
                elif schema is not None and isinstance(content, dict):
                    payload = schema.model_validate(content)
                elif schema is not None and isinstance(content, str):
                    payload = schema.model_validate({"statement": content})
                else:
                    payload = None
                if payload is not None:
                    hits = scan_text_for_secrets(_payload_text_for_scan(payload))
                    if hits:
                        return _reject_sensitive(
                            tool_name=tool.name,
                            domain=domain,
                            scope=scope,
                            hits=hits,
                        )

            output = await run_memory_tool_call(
                lambda: coroutine(**kwargs),
                tool_name=tool.name,
            )
            if schema is not None and tool.name.startswith("recall_"):
                try:
                    raw = output[0] if isinstance(output, tuple) else output
                    parsed = json.loads(raw) if isinstance(raw, str) else raw
                    if isinstance(parsed, list):
                        rows = []
                        for entry in parsed:
                            if not isinstance(entry, dict):
                                continue
                            key = entry.get("key") or entry.get("id")
                            value = entry.get("value", entry)
                            content = unwrap_store_content(value)
                            try:
                                data = schema.model_validate(content).model_dump(mode="json")
                            except Exception:
                                continue
                            rows.append({"id": key, **data})
                        output = json.dumps(rows, ensure_ascii=False)
                except Exception:
                    pass
            result = format_memory_success(output)
            if loop_guard is not None:
                policy = TOOL_LOOP_POLICIES.get(tool.name)
                track_loop = (
                    track_empty
                    and policy is not None
                    and policy.track == ToolLoopTrack.EMPTY_STREAK
                ) or (
                    policy is not None and policy.track == ToolLoopTrack.ONCE_PER_TURN
                )
                if track_loop:
                    loop_guard.record(
                        tool.name,
                        args=args,
                        success=result.success,
                        is_empty=is_json_list_output_empty(
                            output if not isinstance(output, tuple) else output[0]
                        ),
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


class _ProjectManageArgs(BaseModel):
    """项目 manage 工具参数"""

    content: ProjectFactMemory | None = None
    action: Literal["create", "update", "delete"] = "create"
    id: str | None = Field(default=None, description="Memory id for update/delete")


def _build_manage_project_memory(
    *,
    store: Any,
    loop_guard: TurnToolLoopGuard | None,
) -> StructuredTool:
    """项目 manage：公共 BaseStore aput/adelete 写 kind/content 信封"""
    namespacer = langmem_utils.NamespaceTemplate(
        get_memory_domain("canvas").scope_spec("project").namespace
    )

    async def manage_project_memory(
        content: ProjectFactMemory | None = None,
        action: Literal["create", "update", "delete"] = "create",
        id: str | None = None,
        config: Any = None,
    ) -> str:
        """创建/更新/删除一条项目记忆"""
        args = {"action": action, "id": id}
        if loop_guard is not None:
            blocked = loop_guard.pre_check("manage_project_memory", args=args)
            if blocked is not None:
                return _loop_exhausted_message(blocked)
        try:
            if action in ("create", "update") and content is not None:
                payload = (
                    content
                    if isinstance(content, ProjectFactMemory)
                    else ProjectFactMemory.model_validate(content)
                )
                hits = scan_text_for_secrets(_payload_text_for_scan(payload))
                if hits:
                    return _reject_sensitive(
                        tool_name="manage_project_memory",
                        domain="canvas",
                        scope="project",
                        hits=hits,
                    )

            namespace = namespacer(config)

            async def _run() -> str:
                """执行 Store 写入/删除"""
                if action == "delete":
                    if not id:
                        raise ValueError("id required for delete")
                    await store.adelete(namespace, key=str(id))
                    return f"Deleted memory {id}"
                if content is None:
                    raise ValueError("content required for create/update")
                payload = (
                    content
                    if isinstance(content, ProjectFactMemory)
                    else ProjectFactMemory.model_validate(content)
                )
                memory_id = str(id or uuid.uuid4())
                if action == "update" and not id:
                    raise ValueError("id required for update")
                await store.aput(
                    namespace,
                    key=memory_id,
                    value=canonical_project_value(payload),
                )
                return f"{action}d memory {memory_id}"

            output = await run_memory_tool_call(_run, tool_name="manage_project_memory")
            result = format_memory_success(output)
            if loop_guard is not None:
                loop_guard.record(
                    "manage_project_memory",
                    args=args,
                    success=result.success,
                    is_empty=False,
                )
            return result.to_tool_message()
        except Exception as exc:
            if is_memory_unavailable_error(exc):
                return memory_tool_fail(
                    tool_name="manage_project_memory",
                    exc=exc,
                ).to_tool_message()
            if isinstance(exc, ValueError):
                return ToolResult.fail("invalid_arguments", detail=str(exc)).to_tool_message()
            raise

    return StructuredTool.from_function(
        coroutine=manage_project_memory,
        name="manage_project_memory",
        description=PROJECT_MANAGE_DESCRIPTION,
        args_schema=_ProjectManageArgs,
    )


def build_chat_memory_tools(
    loop_guard: TurnToolLoopGuard | None = None,
) -> list[StructuredTool]:
    """构建 Chat 用户 manage/recall 工具"""
    store = get_memory_store()
    if store is None:
        return []
    user = get_memory_domain("chat").scope_spec("user")
    manage = create_manage_memory_tool(
        namespace=user.namespace,
        schema=UserMemory,
        store=store,
        name="manage_user_memory",
        instructions=USER_MANAGE_INSTRUCTIONS_CHAT,
    )
    recall = create_search_memory_tool(
        namespace=user.namespace,
        store=store,
        name="recall_user_memory",
        instructions=USER_RECALL_INSTRUCTIONS,
        response_format="content_and_artifact",
    )
    return [
        _wrap_langmem_tool(
            manage,
            description=USER_MANAGE_DESCRIPTION_CHAT,
            loop_guard=loop_guard,
            track_empty=False,
            schema=UserMemory,
            scan_on_write=True,
            domain="chat",
            scope="user",
        ),
        _wrap_langmem_tool(
            recall,
            description=USER_RECALL_DESCRIPTION,
            loop_guard=loop_guard,
            track_empty=True,
            schema=UserMemory,
        ),
    ]


def build_canvas_memory_tools(
    loop_guard: TurnToolLoopGuard | None = None,
) -> list[StructuredTool]:
    """构建 Canvas 用户 + 项目记忆工具"""
    store = get_memory_store()
    if store is None:
        return []
    domain = get_memory_domain("canvas")
    user = domain.scope_spec("user")
    project = domain.scope_spec("project")
    manage_user = create_manage_memory_tool(
        namespace=user.namespace,
        schema=UserMemory,
        store=store,
        name="manage_user_memory",
        instructions=USER_MANAGE_INSTRUCTIONS_CANVAS,
    )
    recall_user = create_search_memory_tool(
        namespace=user.namespace,
        store=store,
        name="recall_user_memory",
        instructions=USER_RECALL_INSTRUCTIONS,
        response_format="content_and_artifact",
    )
    recall_project = create_search_memory_tool(
        namespace=project.namespace,
        store=store,
        name="recall_project_memory",
        instructions=PROJECT_RECALL_INSTRUCTIONS,
        response_format="content_and_artifact",
    )
    return [
        _wrap_langmem_tool(
            manage_user,
            description=USER_MANAGE_DESCRIPTION_CANVAS,
            loop_guard=loop_guard,
            track_empty=False,
            schema=UserMemory,
            scan_on_write=True,
            domain="canvas",
            scope="user",
        ),
        _wrap_langmem_tool(
            recall_user,
            description=USER_RECALL_DESCRIPTION,
            loop_guard=loop_guard,
            track_empty=True,
            schema=UserMemory,
        ),
        _build_manage_project_memory(store=store, loop_guard=loop_guard),
        _wrap_langmem_tool(
            recall_project,
            description=PROJECT_RECALL_DESCRIPTION,
            loop_guard=loop_guard,
            track_empty=True,
            schema=ProjectFactMemory,
        ),
    ]
