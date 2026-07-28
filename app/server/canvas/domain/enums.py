from enum import IntEnum, StrEnum


class CanvasSessionStatus(IntEnum):
    ACTIVE = 1
    CLOSED = 2


class CanvasNodeKind(StrEnum):
    TEXT = "text"
    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"


class CanvasNodeStatus(StrEnum):
    IDLE = "idle"
    WAITING_INPUTS = "waiting_inputs"
    READY = "ready"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    CANCELLED = "cancelled"


class CanvasPatchOperation(StrEnum):
    CREATE_NODE = "create_node"
    UPDATE_NODE = "update_node"
    DELETE_NODE = "delete_node"
    CONNECT = "connect"
    DISCONNECT = "disconnect"


class CanvasPendingOperationType(StrEnum):
    """tool_pending 审批卡片展示的操作类型"""

    CREATE = "create"
    UPDATE = "update"
    GENERATE = "generate"
    SKILL_WRITE = "skill_write"


class CanvasSourcePort(StrEnum):
    OUTPUT_TEXT = "output_text"
    OUTPUT_ASSET = "output_asset"


class CanvasTargetPort(StrEnum):
    PROMPT_INPUT = "prompt_input"
    REFERENCE_ASSET = "reference_asset"


class CanvasEdgeType(StrEnum):
    DEPENDENCY = "dependency"
