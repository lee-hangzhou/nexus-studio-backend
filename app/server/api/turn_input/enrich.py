from __future__ import annotations

from app.contracts.turn_content import (
    TurnContentBlock,
    TurnMaterialBlock,
    TurnMediaBlock,
    TurnMediaOrigin,
    TurnMediaType,
    TurnNodeBlock,
    TurnSkillBlock,
    TurnTextBlock,
    TurnUserInput,
)
from app.server.assets.services.service import (
    ASSET_SOURCE_AGENT_UPLOAD,
    ASSET_SOURCE_ASSISTANT_OUTPUT,
    ASSET_SOURCE_CHAT_UPLOAD,
    ASSET_SOURCE_GENERATE_MATERIAL,
    ASSET_SOURCE_GENERATE_RESULT,
    ASSET_SOURCE_MANUAL_UPLOAD,
    ASSET_TYPE_AUDIO,
    ASSET_TYPE_IMAGE,
    ASSET_TYPE_VIDEO,
    asset_service,
)
from app.server.exceptions.base import AppError
from app.server.exceptions.codes import ErrorCode
from app.server.ports.product import CanvasPort

_UPLOAD_SOURCES = frozenset(
    {
        ASSET_SOURCE_CHAT_UPLOAD,
        ASSET_SOURCE_AGENT_UPLOAD,
        ASSET_SOURCE_GENERATE_MATERIAL,
        ASSET_SOURCE_ASSISTANT_OUTPUT,
    }
)
_LIBRARY_SOURCES = frozenset(
    {
        ASSET_SOURCE_MANUAL_UPLOAD,
        ASSET_SOURCE_GENERATE_RESULT,
    }
)

_ASSET_TYPE_TO_MEDIA = {
    ASSET_TYPE_IMAGE: TurnMediaType.IMAGE,
    ASSET_TYPE_VIDEO: TurnMediaType.VIDEO,
    ASSET_TYPE_AUDIO: TurnMediaType.AUDIO,
}

_BLOCK_TYPE_TO_MEDIA = {
    TurnMediaType.IMAGE.value: TurnMediaType.IMAGE,
    TurnMediaType.VIDEO.value: TurnMediaType.VIDEO,
    TurnMediaType.AUDIO.value: TurnMediaType.AUDIO,
}


def _media_type_for_asset(asset_type: str, block_type: str) -> TurnMediaType:
    """由资产类型推导 media_type"""
    if block_type == TurnMediaType.FILE.value:
        return TurnMediaType.FILE
    mapped = _ASSET_TYPE_TO_MEDIA.get(asset_type)
    if mapped is None:
        raise AppError(
            ErrorCode.INVALID_PARAMS,
            f"asset type {asset_type} cannot be used as {block_type}",
        )
    return mapped


def _assert_origin_matches_source(origin: TurnMediaOrigin, source_type: str, asset_id: int) -> None:
    """校验 origin 与 assets.source_type 语义一致"""
    if origin == TurnMediaOrigin.LIBRARY and source_type not in _LIBRARY_SOURCES:
        raise AppError(
            ErrorCode.INVALID_PARAMS,
            f"asset {asset_id} origin=library but source_type={source_type}",
        )
    if origin == TurnMediaOrigin.UPLOAD and source_type not in _UPLOAD_SOURCES:
        raise AppError(
            ErrorCode.INVALID_PARAMS,
            f"asset {asset_id} origin=upload but source_type={source_type}",
        )


async def enrich_turn_user_input(
    *,
    user_id: int,
    user_input: TurnUserInput,
    project_id: int | None = None,
    episode_id: int | None = None,
    canvas_port: CanvasPort | None = None,
    allow_node: bool = True,
) -> TurnUserInput:
    """校验归属并补齐展示 URL / media_type; 失败整 turn 拒绝"""
    content = [
        await _enrich_block(
            user_id=user_id,
            block=block,
            project_id=project_id,
            episode_id=episode_id,
            canvas_port=canvas_port,
            allow_node=allow_node,
        )
        for block in user_input.content
    ]
    materials = [
        await _enrich_block(
            user_id=user_id,
            block=block,
            project_id=project_id,
            episode_id=episode_id,
            canvas_port=canvas_port,
            allow_node=allow_node,
        )
        for block in user_input.materials
    ]
    return TurnUserInput(content=content, materials=materials)


async def _enrich_block(
    *,
    user_id: int,
    block: TurnContentBlock | TurnMaterialBlock,
    project_id: int | None,
    episode_id: int | None,
    canvas_port: CanvasPort | None,
    allow_node: bool,
) -> TurnContentBlock | TurnMaterialBlock:
    """enrich 单个 content/material 块"""
    if isinstance(block, TurnNodeBlock):
        if not allow_node:
            raise AppError(ErrorCode.INVALID_PARAMS, "node blocks are not supported on this surface")
        if canvas_port is None or episode_id is None or project_id is None:
            raise AppError(ErrorCode.INVALID_PARAMS, "node blocks require canvas context")
        node = await canvas_port.get_node(episode_id, block.node_id)
        if node is None:
            raise AppError(ErrorCode.INVALID_PARAMS, f"node not found: {block.node_id}")
        return block

    if isinstance(block, TurnMediaBlock):
        row = await asset_service.require_owned(user_id=user_id, asset_id=block.asset_id)
        _assert_origin_matches_source(block.origin, row.source_type, block.asset_id)
        if project_id is not None and row.project_id is not None and int(row.project_id) != int(project_id):
            raise AppError(ErrorCode.INVALID_PARAMS, f"asset {block.asset_id} not in project")
        if block.type == TurnMediaType.FILE.value:
            media_type = TurnMediaType.FILE
        else:
            media_type = _media_type_for_asset(row.asset_type, block.type)
            expected = _BLOCK_TYPE_TO_MEDIA[block.type]
            if media_type != expected:
                raise AppError(
                    ErrorCode.INVALID_PARAMS,
                    f"asset {block.asset_id} type {row.asset_type} does not match block {block.type}",
                )
        url = asset_service.preview_url(row.storage_key)
        return block.model_copy(
            update={
                "media_type": media_type,
                "url": url,
            }
        )

    if isinstance(block, (TurnTextBlock, TurnSkillBlock)):
        return block

    raise AppError(ErrorCode.INVALID_PARAMS, f"unsupported turn block type: {type(block)!r}")
