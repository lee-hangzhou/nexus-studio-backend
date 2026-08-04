"""创作提示词助手 system prompt 组装。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.agent.chat.prompt.composer import PromptComposer
from app.agent.chat.prompt.types import TurnPromptContext
from app.contracts.composer_prompt import GenerateComposerContext
from app.contracts.turn_content import TurnReferenceIndex, format_turn_references_block
from app.server.infra.config import settings

_CORE_POLICY_PATH = Path(__file__).resolve().parent / "prompts" / "core_policy.md"


def load_prompt_assistant_core_policy() -> str:
    """加载创作提示词助手核心策略。"""
    return _CORE_POLICY_PATH.read_text(encoding="utf-8").strip()


def format_composer_context_block(snapshot: GenerateComposerContext | None) -> str:
    """格式化当前创作器草稿上下文块；无快照则返回空串。"""
    if snapshot is None:
        return ""
    lines = [
        "## 当前创作器草稿",
        f"- kind: {snapshot.kind}",
        f"- model_id: {snapshot.model_id or '（未选）'}",
        f"- ratio: {snapshot.ratio if snapshot.ratio is not None else '（未设）'}",
        f"- resolution: {snapshot.resolution if snapshot.resolution is not None else '（未设）'}",
        f"- count: {snapshot.count if snapshot.count is not None else '（未设）'}",
        f"- duration: {snapshot.duration if snapshot.duration is not None else '（未设）'}",
        f"- reference_mode: {snapshot.reference_mode if snapshot.reference_mode is not None else '（未设）'}",
        f"- ref_asset_ids: {list(snapshot.ref_asset_ids)}",
        f"- prompt: {snapshot.prompt if snapshot.prompt else '（空）'}",
    ]
    if snapshot.content:
        lines.append("- content (structured segments):")
        for index, segment in enumerate(snapshot.content, start=1):
            payload = segment.model_dump(mode="json")
            lines.append(f"  {index}. {payload}")
    else:
        lines.append("- content_segments: 0")
    lines.append(
        "参数仅供理解用户当前设置；禁止通过工具修改参数或提交生成。"
        "写回创作器时 apply_composer_prompt.content 须与上述分段形状一致。"
    )
    return "\n".join(lines)


def build_prompt_assistant_system(
    ctx: TurnPromptContext,
    *,
    composer_context: GenerateComposerContext | None,
    turn_references_block: TurnReferenceIndex | None,
    attachment_context_block: str = "",
) -> str:
    """组装创作提示词助手本轮 system prompt。"""
    parts = [
        load_prompt_assistant_core_policy(),
        PromptComposer.build_capability_brief(ctx),
    ]
    composer_block = format_composer_context_block(composer_context)
    if composer_block:
        parts.append(composer_block)
    if turn_references_block is not None:
        refs = format_turn_references_block(turn_references_block)
        if refs:
            parts.append(refs)
    if attachment_context_block.strip():
        parts.append(attachment_context_block.strip())
    turn_media_brief = PromptComposer.build_turn_media_brief(ctx)
    if turn_media_brief:
        parts.append(turn_media_brief)
    parts.append(PromptComposer.build_context_clock(now=datetime.now(ZoneInfo(settings.CHAT_CONTEXT_TIMEZONE))))
    return "\n\n".join(parts)
