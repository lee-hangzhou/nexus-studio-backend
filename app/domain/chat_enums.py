from enum import Enum, IntEnum


class ChatConversationStatus(IntEnum):
    ACTIVE = 1
    CLOSED = 2


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
