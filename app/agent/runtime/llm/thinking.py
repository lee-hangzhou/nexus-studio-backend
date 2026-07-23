"""Minimal AIMessage reasoning helpers shared by the agent runner."""

from __future__ import annotations

from langchain_core.messages import AIMessage

REASONING_CONTENT_KEY = "reasoning_content"


def reasoning_content_from_message(message: AIMessage) -> str | None:
    """Return stored reasoning text for OpenAI round-trip, if any."""
    kwargs = message.additional_kwargs or {}
    value = kwargs.get(REASONING_CONTENT_KEY)
    if isinstance(value, str) and value:
        return value
    return None


def ai_message_with_reasoning(message: AIMessage, reasoning: str | None) -> AIMessage:
    """Attach reasoning_content to AIMessage for checkpoint / outbound replay."""
    if not reasoning:
        return message
    kwargs = dict(message.additional_kwargs or {})
    kwargs[REASONING_CONTENT_KEY] = reasoning
    return message.model_copy(update={"additional_kwargs": kwargs})


def build_ai_message(
    *,
    content: str,
    tool_calls: list | None = None,
    reasoning: str | None = None,
) -> AIMessage:
    """Construct AIMessage with optional reasoning_content in additional_kwargs."""
    message = AIMessage(content=content, tool_calls=list(tool_calls or []))
    return ai_message_with_reasoning(message, reasoning)
