from dataclasses import dataclass
from enum import Enum
from typing import Any, Literal

from app.agent.runtime.llm.thinking import (
    REASONING_CONTENT_KEY,
    ai_message_with_reasoning,
    build_ai_message,
    reasoning_content_from_message,
)

__all__ = [
    "DEFAULT_TAG_PAIRS",
    "REASONING_CONTENT_KEY",
    "ThinkTagPair",
    "ThinkTagStreamState",
    "ThinkingConfig",
    "ThinkingMode",
    "TokenPiece",
    "ai_message_with_reasoning",
    "build_ai_message",
    "extract_reasoning_from_openai_message",
    "reasoning_content_from_message",
    "thinking_config_from_registry",
]


class ThinkingMode(str, Enum):
    REASONING_FIELD = "reasoning_field"
    CONTENT_TAGS = "content_tags"
    NONE = "none"


@dataclass(frozen=True)
class ThinkTagPair:
    open: str
    close: str


def _think_tag_open() -> str:
    return "<" + "think" + ">"


def _think_tag_close() -> str:
    return "<" + "/think" + ">"


DEFAULT_TAG_PAIRS = (
    ThinkTagPair(open=_think_tag_open(), close=_think_tag_close()),
    ThinkTagPair(open="<think>", close="</think>"),
)


@dataclass(frozen=True)
class ThinkingConfig:
    mode: ThinkingMode = ThinkingMode.CONTENT_TAGS
    reasoning_fields: tuple[str, ...] = ("reasoning_content", "reasoning", "thinking")


@dataclass
class ThinkTagStreamState:
    think_open: bool = False
    active_close: str = ""
    pending: str = ""


@dataclass(frozen=True)
class TokenPiece:
    lane: Literal["answer", "think"]
    text: str


def extract_reasoning_from_openai_message(message: dict[str, Any]) -> str | None:
    """Pick the first non-empty reasoning field from an OpenAI assistant message."""
    for field in ThinkingConfig().reasoning_fields:
        value = message.get(field)
        if isinstance(value, str) and value:
            return value
    return None


def thinking_config_from_registry(raw: dict | None) -> ThinkingConfig:
    if not raw:
        return ThinkingConfig()
    mode_str = str(raw.get("mode", "content_tags"))
    try:
        mode = ThinkingMode(mode_str)
    except ValueError:
        mode = ThinkingMode.CONTENT_TAGS
    fields = raw.get("reasoning_fields")
    if isinstance(fields, list) and fields:
        reasoning_fields = tuple(str(f) for f in fields)
    else:
        reasoning_fields = ThinkingConfig().reasoning_fields
    return ThinkingConfig(mode=mode, reasoning_fields=reasoning_fields)
