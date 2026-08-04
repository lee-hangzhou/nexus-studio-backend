from enum import Enum, IntEnum, StrEnum


class ChatConversationStatus(IntEnum):
    ACTIVE = 1
    CLOSED = 2


class ChatConversationKind(StrEnum):
    """会话产品形态；prompt_assistant 为每人常驻创作提示词助手。"""

    CHAT = "chat"
    PROMPT_ASSISTANT = "prompt_assistant"


class ChatMessageRole(IntEnum):
    USER = 1
    ASSISTANT = 2
    SYSTEM = 3
    TOOL = 4


class ChatAttachmentStatus(IntEnum):
    UPLOADED = 1
    PARSING = 2
    READY = 3
    FAILED = 4
    DETACHED = 5


class AttachmentSource(str, Enum):
    USER_UPLOAD = "user_upload"
    ASSISTANT_TOOL = "assistant_tool"
