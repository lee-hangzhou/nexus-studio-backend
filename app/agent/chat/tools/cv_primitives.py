"""CV primitive tools for gap/template analysis (not site-specific solvers)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

from langchain.tools import InjectedToolArg
from langchain_core.runnables import RunnableConfig

from app.agent.chat.cv import gap_column, image_info, template_match
from app.agent.chat.tools.result import (
    CV_LOW_CONFIDENCE,
    FILE_NOT_FOUND,
    INVALID_ARGUMENTS,
    SANDBOX_ERROR,
    ToolResult,
)

_CV_CONFIDENCE_THRESHOLD = 0.35


def _workspace_from_config(config: RunnableConfig | None) -> Path:
    return Path((config.get("configurable") or {}).get("workspace") or "/tmp")


def _resolve_workspace_path(workspace: Path, rel: str) -> Path:
    candidate = (workspace / rel.lstrip("/")).resolve()
    workspace_resolved = workspace.resolve()
    if workspace_resolved not in candidate.parents and candidate != workspace_resolved:
        raise ValueError("path escapes workspace")
    return candidate


async def cv_image_info(
    image_path: str,
    *,
    config: Annotated[RunnableConfig, InjectedToolArg] = None,
) -> str:
    """Return width, height, channels for a workspace image (pixel index coordinates)."""
    workspace = _workspace_from_config(config)
    try:
        path = _resolve_workspace_path(workspace, image_path)
    except ValueError as exc:
        return ToolResult.fail(INVALID_ARGUMENTS, detail=str(exc)).to_tool_message()
    if not path.is_file():
        return ToolResult.fail(FILE_NOT_FOUND, detail=str(image_path)).to_tool_message()
    try:
        payload = image_info.image_info(path)
        payload["path"] = image_path
        return ToolResult.ok(json.dumps(payload, ensure_ascii=False)).to_tool_message()
    except Exception as exc:
        return ToolResult.fail(SANDBOX_ERROR, detail=str(exc)).to_tool_message()


async def cv_match_template(
    image_path: str,
    template_path: str,
    roi: dict[str, int] | None = None,
    *,
    config: Annotated[RunnableConfig, InjectedToolArg] = None,
) -> str:
    """Template match in image pixel coordinates; returns dx, dy, confidence."""
    workspace = _workspace_from_config(config)
    try:
        img = _resolve_workspace_path(workspace, image_path)
        tpl = _resolve_workspace_path(workspace, template_path)
    except ValueError as exc:
        return ToolResult.fail(INVALID_ARGUMENTS, detail=str(exc)).to_tool_message()
    if not img.is_file() or not tpl.is_file():
        return ToolResult.fail(FILE_NOT_FOUND, detail="image or template missing").to_tool_message()
    try:
        payload = template_match.match_template(image_path=img, template_path=tpl, roi=roi)
        if float(payload.get("confidence") or 0) < _CV_CONFIDENCE_THRESHOLD:
            return ToolResult.fail(
                CV_LOW_CONFIDENCE,
                detail=json.dumps(payload, ensure_ascii=False),
            ).to_tool_message()
        return ToolResult.ok(json.dumps(payload, ensure_ascii=False)).to_tool_message()
    except Exception as exc:
        return ToolResult.fail(SANDBOX_ERROR, detail=str(exc)).to_tool_message()


async def cv_find_gap_x(
    background_path: str | None = None,
    piece_path: str | None = None,
    image_path: str | None = None,
    *,
    config: Annotated[RunnableConfig, InjectedToolArg] = None,
) -> str:
    """Find gap x candidates via column projection / background-piece diff."""
    workspace = _workspace_from_config(config)
    try:
        bg = _resolve_workspace_path(workspace, background_path) if background_path else None
        piece = _resolve_workspace_path(workspace, piece_path) if piece_path else None
        img = _resolve_workspace_path(workspace, image_path) if image_path else None
    except ValueError as exc:
        return ToolResult.fail(INVALID_ARGUMENTS, detail=str(exc)).to_tool_message()
    if bg and not bg.is_file():
        return ToolResult.fail(FILE_NOT_FOUND, detail=background_path or "").to_tool_message()
    if piece and not piece.is_file():
        return ToolResult.fail(FILE_NOT_FOUND, detail=piece_path or "").to_tool_message()
    if img and not img.is_file():
        return ToolResult.fail(FILE_NOT_FOUND, detail=image_path or "").to_tool_message()
    try:
        payload = gap_column.find_gap_x(
            background_path=bg,
            piece_path=piece,
            image_path=img,
        )
        if float(payload.get("confidence") or 0) < _CV_CONFIDENCE_THRESHOLD:
            return ToolResult.fail(
                CV_LOW_CONFIDENCE,
                detail=json.dumps(payload, ensure_ascii=False),
            ).to_tool_message()
        return ToolResult.ok(json.dumps(payload, ensure_ascii=False)).to_tool_message()
    except Exception as exc:
        return ToolResult.fail(SANDBOX_ERROR, detail=str(exc)).to_tool_message()
