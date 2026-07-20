from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langchain_core.messages import BaseMessage, HumanMessage

from app.chat.vision.bytes import assert_base64_within_limit, encode_base64, load_attachment_bytes
from app.chat.vision.types import IMAGE_REF_TYPE, TEXT_BLOCK_TYPE


@dataclass(frozen=True)
class VisionBuildContext:
    hydrate_attachment_ids: frozenset[int]
    attachment_paths: dict[int, str]
    workspace_root: Path | None


EMPTY_VISION_CONTEXT = VisionBuildContext(frozenset(), {}, None)


def build_vision_context(
    *,
    hydrate_attachment_ids: list[int] | frozenset[int] | None = None,
    attachment_binary_paths: dict[int, str] | None = None,
    conversation_workspace: str | Path | None = None,
) -> VisionBuildContext:
    raw_ids = hydrate_attachment_ids or []
    ids = frozenset(int(x) for x in raw_ids)
    paths = dict(attachment_binary_paths or {})
    root = Path(str(conversation_workspace)) if conversation_workspace else None
    return VisionBuildContext(ids, paths, root)


def should_hydrate_ref(attachment_id: int, ctx: VisionBuildContext) -> bool:
    return attachment_id in ctx.hydrate_attachment_ids


def iter_human_content_blocks(message: HumanMessage) -> list[dict[str, Any]]:
    content = message.content
    if isinstance(content, list):
        return [block for block in content if isinstance(block, dict)]
    if isinstance(content, str):
        return [{"type": TEXT_BLOCK_TYPE, "text": content}]
    return [{"type": TEXT_BLOCK_TYPE, "text": str(content or "")}]


def count_hydratable_image_refs(messages: list[BaseMessage], ctx: VisionBuildContext) -> int:
    total = 0
    for message in messages:
        if not isinstance(message, HumanMessage):
            continue
        for block in iter_human_content_blocks(message):
            if block.get("type") != IMAGE_REF_TYPE:
                continue
            aid = block.get("attachment_id")
            if isinstance(aid, int) and should_hydrate_ref(aid, ctx):
                total += 1
    return total


def load_hydrated_image(attachment_id: int, mime_type: str, ctx: VisionBuildContext) -> tuple[str, str]:
    rel = ctx.attachment_paths.get(attachment_id, "")
    raw, detected_mime = load_attachment_bytes(
        attachment_id=attachment_id,
        relative_path=rel,
        workspace_root=ctx.workspace_root,
    )
    effective_mime = mime_type or detected_mime
    b64 = encode_base64(raw)
    assert_base64_within_limit(b64)
    return b64, effective_mime
