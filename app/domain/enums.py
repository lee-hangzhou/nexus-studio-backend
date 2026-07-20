from enum import IntEnum


class ProjectStatus(IntEnum):
    """项目生命周期状态"""

    ACTIVE = 1
    PAUSED = 2
    COMPLETED = 3
    CANCELLED = 4


class GatewayTaskStatus(IntEnum):
    """模型网关异步任务状态"""

    CREATED = 1
    QUEUED = 2
    WAITING = 3
    RUNNING = 4
    SUCCEEDED = 5
    FAILED = 6
    CANCELLED = 7
