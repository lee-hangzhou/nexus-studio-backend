from __future__ import annotations

import json
from dataclasses import dataclass

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field, ValidationError

from app.agent.chat.llm import get_adapter
from app.agent.chat.llm.adapter import ModelAdapter
from app.agent.chat.llm.registry import get_model_spec
from app.server.chat.domain.enums import ChatMessageRole
from app.server.chat.persistence.attachments import ChatAttachments
from app.server.chat.persistence.conversations import ChatConversations
from app.server.chat.persistence.messages import ChatMessages
from app.server.chat.services.constants import CONVERSATION_TITLE_MAX_LEN, DEFAULT_CONVERSATION_TITLE
from app.server.infra.config import settings
from app.server.infra.gateway import gateway_client
from app.server.infra.logger import log_exception, logger

_ZERO_WIDTH = ("\u200b", "\u200c", "\u200d", "\ufeff")


@dataclass(frozen=True)
class TitleApplyResult:
    applied: bool
    title: str | None = None
    updated_at: str | None = None


class ConversationTitleDecision(BaseModel):
    title: str = Field(min_length=1, max_length=CONVERSATION_TITLE_MAX_LEN)


def normalize_title_text(text: str) -> str:
    """规范化标题空白与包裹引号"""
    cleaned = text.strip()
    for ch in _ZERO_WIDTH:
        cleaned = cleaned.replace(ch, "")
    cleaned = " ".join(cleaned.split())
    if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in "\"'「」":
        cleaned = cleaned[1:-1].strip()
    return cleaned


def clamp_title_length(text: str, max_len: int = CONVERSATION_TITLE_MAX_LEN) -> str:
    """截断超长标题并加省略号"""
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"


def is_placeholder_title(title: str, *, placeholder: str = DEFAULT_CONVERSATION_TITLE) -> bool:
    """是否仍是未命名占位标题"""
    return normalize_title_text(title) == normalize_title_text(placeholder)


async def attachment_filenames(
    *,
    user_id: int,
    conversation_id: int,
    attachment_ids: list[int],
) -> list[str]:
    """按附件 id 取文件名列表"""
    if not attachment_ids:
        return []
    rows = await ChatAttachments.filter(
        id__in=attachment_ids,
        user_id=user_id,
        conversation_id=conversation_id,
    )
    return [row.filename for row in rows]


def _gateway_error_code(response: dict) -> int | None:
    """解析网关业务错误码, 成功返回 None"""
    code = response.get("code")
    if code is None:
        return None
    if isinstance(code, int) and code in (0, 200):
        return None
    if isinstance(code, str) and code in ("0", "200"):
        return None
    return int(code) if isinstance(code, int) else None


def parse_title_decision(response: dict, *, adapter: ModelAdapter) -> ConversationTitleDecision:
    """从 chat completion 响应解析标题 JSON"""
    parsed = adapter.parse_response(response)
    content = str(parsed.get("content") or "").strip()
    if not content:
        raise ValueError("chat completion returned empty content")
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        payload = {"title": content}
    return ConversationTitleDecision.model_validate(payload)


async def propose_sidebar_title_via_llm(
    *,
    user_content: str,
    attachment_filenames_list: list[str],
    model_key: str,
    placeholder_title: str,
    log_event_prefix: str,
    log_context: dict | None = None,
) -> str | None:
    """用首条用户消息经 LLM 提议侧栏标题, 失败返回 None"""
    ctx = log_context or {}
    attachment_hint = "、".join(attachment_filenames_list) if attachment_filenames_list else "无"
    messages = [
        SystemMessage(
            content=(
                "你是会话标题助手。根据用户首条消息（及附件名），为侧栏生成简短中文主题标题（6–20 字，"
                f"不超过 {CONVERSATION_TITLE_MAX_LEN} 字）。\n"
                "概括用户想做的事或主题，不要照搬用户原句，不要完整问句，不要寒暄，不要引号。\n"
                "只输出 JSON：{\"title\": \"...\"}"
            ),
        ),
        HumanMessage(
            content=(
                f"用户首条消息：{user_content}\n"
                f"附件：{attachment_hint}"
            ),
        ),
    ]
    spec = get_model_spec(model_key)
    adapter = get_adapter(spec.family)
    payload = adapter.build_params(messages, spec.gateway_model, stream=False)
    try:
        response = await gateway_client.openai_chat_completion(
            payload,
            timeout_sec=min(settings.CHAT_GATEWAY_TIMEOUT_SEC, 60),
        )
        error_code = _gateway_error_code(response)
        if error_code is not None:
            logger.error(
                f"{log_event_prefix}.gateway_error",
                model_key=model_key,
                gateway_model=spec.gateway_model,
                code=error_code,
                message=response.get("message"),
                **ctx,
            )
            return None
        decision = parse_title_decision(response, adapter=adapter)
    except (ValidationError, ValueError, Exception) as exc:
        log_exception(
            f"{log_event_prefix}.llm_failed",
            exc=exc,
            model_key=model_key,
            gateway_model=spec.gateway_model,
            **ctx,
        )
        return None

    new_title = clamp_title_length(normalize_title_text(decision.title))
    if not new_title or is_placeholder_title(new_title, placeholder=placeholder_title):
        logger.info(
            f"{log_event_prefix}.llm_skipped",
            reason="empty_or_placeholder",
            **ctx,
        )
        return None
    return new_title


async def generate_conversation_title_via_llm(
    *,
    user_id: int,
    conversation_id: int,
    user_content: str,
    attachment_ids: list[int],
    model_key: str,
) -> TitleApplyResult:
    """首轮且仍为占位标题时生成并写回 Chat 会话标题"""
    row = await ChatConversations.get(id=conversation_id, user_id=user_id)
    if not is_placeholder_title(row.title):
        logger.info(
            "chat.conversation_title.llm_skipped",
            conversation_id=conversation_id,
            reason="not_placeholder",
        )
        return TitleApplyResult(applied=False)

    expected_title = row.title
    attachment_filenames_list = await attachment_filenames(
        user_id=user_id,
        conversation_id=conversation_id,
        attachment_ids=attachment_ids,
    )
    new_title = await propose_sidebar_title_via_llm(
        user_content=user_content,
        attachment_filenames_list=attachment_filenames_list,
        model_key=model_key,
        placeholder_title=DEFAULT_CONVERSATION_TITLE,
        log_event_prefix="chat.conversation_title",
        log_context={"conversation_id": conversation_id},
    )
    if new_title is None:
        return TitleApplyResult(applied=False)

    row = await ChatConversations.get(id=conversation_id, user_id=user_id)
    if row.title != expected_title:
        logger.info(
            "chat.conversation_title.llm_skipped",
            conversation_id=conversation_id,
            reason="title_changed",
        )
        return TitleApplyResult(applied=False)

    row.title = new_title
    await row.save(update_fields=["title", "updated_at"])
    updated_at = row.updated_at.isoformat() if row.updated_at else None
    logger.info(
        "chat.conversation_title.llm_ok",
        conversation_id=conversation_id,
        title=new_title,
    )
    return TitleApplyResult(applied=True, title=new_title, updated_at=updated_at)


async def is_first_user_message(conversation_id: int) -> bool:
    """会话是否仅有一条用户消息"""
    count = await ChatMessages.filter(
        conversation_id=conversation_id,
        role=int(ChatMessageRole.USER),
    ).count()
    return count == 1
