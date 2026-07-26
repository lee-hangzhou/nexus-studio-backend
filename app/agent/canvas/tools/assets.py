from __future__ import annotations

import json

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from app.agent.chat.tools.result import ToolResult
from app.agent.runtime.ports import get_assets_port
from app.server.ports.product import AssetDTO


class ListAssetsInput(BaseModel):
    """list_assets 工具入参"""

    asset_type: str | None = Field(default=None, description="可选资产类型：image | video | audio | text")
    source_type: str | None = Field(
        default=None,
        description="可选来源过滤，例如 chat_upload、generate_result、canvas_node_output",
    )
    limit: int = Field(default=20, ge=1, le=100)


class GetAssetInput(BaseModel):
    """get_asset 工具入参"""

    asset_id: int


def _asset_payload(asset: AssetDTO) -> dict:
    """资产 DTO 转工具可返回的结构化 payload"""
    return {
        "id": asset.id,
        "asset_type": asset.asset_type,
        "mime_type": asset.mime_type,
        "filename": asset.filename,
        "source_type": asset.source_type,
        "source_id": asset.source_id,
        "metadata": asset.metadata,
        "status": asset.status,
        "preview_url": asset.preview_url,
    }


def build_list_assets_tool(user_id: int) -> StructuredTool:
    """构建 list_assets 工具, 限定当前用户资产"""

    async def _run(asset_type: str | None = None, source_type: str | None = None, limit: int = 20) -> str:
        """按类型与来源查询可复用系统资产"""
        items = await get_assets_port().list_assets(
            user_id=user_id,
            asset_type=asset_type,
            source_type=source_type,
            limit=limit,
        )
        return ToolResult.ok(
            json.dumps({"items": [_asset_payload(item) for item in items]}, ensure_ascii=False)
        ).to_tool_message()

    return StructuredTool.from_function(
        coroutine=_run,
        name="list_assets",
        description="列出当前用户可复用的系统资产。需要在生成中引用媒体时，请使用返回的 asset_id。",
        args_schema=ListAssetsInput,
    )


def build_get_asset_tool(user_id: int) -> StructuredTool:
    """构建 get_asset 单资产查询工具"""

    async def _run(asset_id: int) -> str:
        """按 asset_id 读取资产, 不存在或不属于当前用户时结构化失败"""
        asset = await get_assets_port().get_asset(user_id=user_id, asset_id=asset_id)
        if asset is None:
            return ToolResult.fail("asset_not_found", detail=str(asset_id)).to_tool_message()
        return ToolResult.ok(json.dumps(_asset_payload(asset), ensure_ascii=False)).to_tool_message()

    return StructuredTool.from_function(
        coroutine=_run,
        name="get_asset",
        description="按 asset_id 获取一个系统资产，包含媒体类型、来源、元信息和预览 URL。",
        args_schema=GetAssetInput,
    )
