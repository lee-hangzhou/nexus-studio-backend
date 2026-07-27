from __future__ import annotations

import json

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from app.agent.runtime.ports import get_user_skill_port
from app.agent.runtime.tools.result import INVALID_ARGUMENTS, ToolResult
from app.agent.runtime.tools.user_skill_protocol import (
    SKILL_NOT_FOUND,
    SKILL_REVISION_CONFLICT,
    WRITE_USER_SKILL_FILE,
)
from app.server.exceptions.base import AppError
from app.server.exceptions.codes import ErrorCode


class WriteUserSkillFileInput(BaseModel):
    """write_user_skill_file 工具入参"""

    path: str = Field(min_length=1, description="技能路径, 不含首尾斜杠")
    name: str = Field(min_length=1, description="技能显示名称")
    description: str | None = Field(
        default=None,
        description="技能描述；省略时覆盖保留原描述，新建由网关补全",
    )
    content: str = Field(default="", description="技能 Markdown 正文")
    revision: int | None = Field(default=None, description="覆盖已有文件时必填 revision")


def build_write_user_skill_file_tool(
    *,
    surface: str,
    user_id: int,
) -> StructuredTool:
    """构建 write_user_skill_file 工具, scope 固定 user"""

    async def _run(
        path: str,
        name: str,
        description: str | None = None,
        content: str = "",
        revision: int | None = None,
    ) -> str:
        port = get_user_skill_port()
        try:
            item = await port.write_user_file(
                surface=surface,
                user_id=user_id,
                path=path,
                name=name,
                description=description,
                content=content,
                revision=revision,
            )
        except AppError as exc:
            if exc.code == int(ErrorCode.USER_SKILL_REVISION_CONFLICT):
                code = SKILL_REVISION_CONFLICT
            elif exc.code == int(ErrorCode.RESOURCE_NOT_FOUND):
                code = SKILL_NOT_FOUND
            else:
                code = INVALID_ARGUMENTS
            return ToolResult.fail(code, detail=exc.message).to_tool_message()
        payload = {
            "path": item.path,
            "scope": item.scope,
            "surface": surface,
            "description": item.description,
            "revision": item.revision,
        }
        return ToolResult.ok(json.dumps(payload, ensure_ascii=False)).to_tool_message()

    return StructuredTool.from_function(
        coroutine=_run,
        name=WRITE_USER_SKILL_FILE,
        description=(
            "Create or update a user-scoped custom skill file for the current surface. "
            "Scope is always user. Overwrite requires revision from a prior write result or skill list."
        ),
        args_schema=WriteUserSkillFileInput,
    )
