from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TurnContext:
    """Factual per-turn switches only — no user-text intent classification."""

    has_attachments: bool
    enable_tools: bool
    enable_materialize: bool
    inject_attachment_manifest: bool


def build_turn_context(
    *,
    has_attachments: bool,
    enable_tools: bool,
) -> TurnContext:
    return TurnContext(
        has_attachments=has_attachments,
        enable_tools=enable_tools,
        enable_materialize=has_attachments,
        inject_attachment_manifest=has_attachments,
    )
