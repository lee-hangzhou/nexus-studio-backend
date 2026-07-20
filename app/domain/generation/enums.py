from enum import IntEnum, StrEnum


class GenerationKind(StrEnum):
    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"


class ReferenceMode(IntEnum):
    FIRST_FRAME = 1
    FIRST_LAST_FRAME = 2
    OMNI_REFERENCE = 3
    VIDEO_EDIT = 4


class MaterialType(IntEnum):
    VIDEO = 1
    IMAGE = 2
    AUDIO = 3


class GatewayContentType(IntEnum):
    TEXT = 1
    IMAGE = 2
    VIDEO = 3
    AUDIO = 4


class GatewayModelTaskType(IntEnum):
    CHAT = 1
    IMAGE = 2
    VIDEO = 3
    TTS = 8
    EMBEDDING = 9


class GenerationTaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
