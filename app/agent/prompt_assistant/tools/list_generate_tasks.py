"""list_generate_tasks：查询当前用户创作任务历史。"""

from __future__ import annotations

import json
from typing import Literal

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ConfigDict, Field

from app.agent.chat.tools.result import INVALID_ARGUMENTS, ToolResult
from app.agent.runtime.ports import get_generation_port
from app.server.generation.domain.enums import GenerationKind
from app.server.generation.schemas import GenerateTaskListRequest


class ListGenerateTasksInput(BaseModel):
    """list_generate_tasks 工具入参。"""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["image", "video", "audio"] | None = Field(
        default=None,
        description="可选媒体类型过滤；省略则返回全部类型。",
    )
    page_size: int = Field(
        default=10,
        ge=1,
        le=20,
        description="返回条数，默认 10，最大 20。",
    )
    query: str = Field(
        default="",
        description="可选关键词，匹配任务提示词等可检索字段。",
    )


def build_list_generate_tasks_tool(*, user_id: int) -> StructuredTool:
    """构建 list_generate_tasks，仅查询当前用户任务。"""

    async def _run(
        kind: Literal["image", "video", "audio"] | None = None,
        page_size: int = 10,
        query: str = "",
    ) -> str:
        try:
            args = ListGenerateTasksInput.model_validate(
                {"kind": kind, "page_size": page_size, "query": query}
            )
        except Exception as exc:
            return str(
                ToolResult.fail(
                    INVALID_ARGUMENTS,
                    detail=str(exc) or "invalid list_generate_tasks arguments",
                ).to_tool_message()
            )
        req = GenerateTaskListRequest(
            kind=GenerationKind(args.kind) if args.kind is not None else None,
            page_size=args.page_size,
            query=args.query,
        )
        result = await get_generation_port().list_tasks(user_id, req)
        return str(
            ToolResult.ok(
                json.dumps(result.model_dump(mode="json"), ensure_ascii=False)
            ).to_tool_message()
        )

    return StructuredTool.from_function(
        coroutine=_run,
        name="list_generate_tasks",
        description=(
            "列出当前用户的创作生成任务（历史与状态）。"
            "用于参考过往提示词、结果或复用素材线索。"
            "不要替用户提交新任务。"
            "Args: kind（可选 image|video|audio）、page_size（1-20）、query（可选）。"
            "返回 items[]、has_more、next_cursor。"
        ),
        args_schema=ListGenerateTasksInput,
    )
