from __future__ import annotations

from app.canvas.node_submit.types import MentionMediaKind
from app.domain.canvas.enums import CanvasNodeKind

MENTION_TYPE_LABEL: dict[MentionMediaKind, str] = {
    "image": "图片",
    "video": "视频",
    "audio": "音频",
    "text": "文本",
}


def node_kind_to_media_kind(kind: CanvasNodeKind) -> MentionMediaKind:
    if kind == CanvasNodeKind.VIDEO:
        return "video"
    if kind == CanvasNodeKind.AUDIO:
        return "audio"
    if kind == CanvasNodeKind.TEXT:
        return "text"
    return "image"


def assign_media_label(kind: MentionMediaKind, slot: int) -> str:
    return f"{MENTION_TYPE_LABEL[kind]}{slot}"
