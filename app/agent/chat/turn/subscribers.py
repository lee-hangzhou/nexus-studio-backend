"""Chat turn side-effect subscribers (recording / browser / persistence / resume)."""

from __future__ import annotations

from app.agent.chat.turn.session import ChatTurnSession
from app.agent.chat.turn.subscribers_browser import ChatBrowserBlockedSubscriber
from app.agent.chat.turn.subscribers_persistence import ChatPersistenceSubscriber
from app.agent.chat.turn.subscribers_recording import ChatRecordingSubscriber
from app.agent.chat.turn.subscribers_resume import (
    ChatAbortGateSubscriber,
    ChatResumeControlHook,
    ChatResumeSubscriber,
)
from app.agent.runtime.turn_engine.subscribers import TurnSubscriber

__all__ = [
    "ChatAbortGateSubscriber",
    "ChatBrowserBlockedSubscriber",
    "ChatPersistenceSubscriber",
    "ChatRecordingSubscriber",
    "ChatResumeControlHook",
    "ChatResumeSubscriber",
    "ChatTurnSession",
    "build_chat_lifecycle_subscribers",
]

# Re-export session for callers that import from subscribers.


def build_chat_lifecycle_subscribers(session: ChatTurnSession) -> list[TurnSubscriber]:
    """Compose chat main-turn side-effect adapters (SSE terminals owned by SseTurnSubscriber)."""
    return [
        ChatRecordingSubscriber(session),
        ChatBrowserBlockedSubscriber(session),
        ChatPersistenceSubscriber(session),
    ]
