from enum import IntEnum


class ProjectStatus(IntEnum):
    """项目生命周期状态"""

    ACTIVE = 1
    PAUSED = 2
    COMPLETED = 3
    CANCELLED = 4
