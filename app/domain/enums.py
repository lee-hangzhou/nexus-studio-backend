from enum import IntEnum


class ProjectStatus(IntEnum):
    """项目生命周期状态"""

    ACTIVE = 1
    PAUSED = 2
    COMPLETED = 3
    CANCELLED = 4


class GatewayTaskStatus(IntEnum):
    """模型网关异步任务状态，与 union_lm 任务状态枚举对齐"""

    CREATED = 1
    QUEUED = 2
    WAITING = 3
    RUNNING = 4
    SUCCEEDED = 5
    FAILED = 6
    CANCELLED = 7

    @property
    def is_terminal(self) -> bool:
        """是否已进入不可再被非终态覆盖的结束态"""
        return self in TERMINAL_GATEWAY_TASK_STATUSES

    @property
    def is_non_terminal(self) -> bool:
        """是否仍可能被网关观察或回调继续推进"""
        return self in NON_TERMINAL_GATEWAY_TASK_STATUSES


# 终态：成功 / 失败 / 取消后，本地任务状态不得再被中间态回写
TERMINAL_GATEWAY_TASK_STATUSES: frozenset[GatewayTaskStatus] = frozenset(
    {
        GatewayTaskStatus.SUCCEEDED,
        GatewayTaskStatus.FAILED,
        GatewayTaskStatus.CANCELLED,
    }
)
# 非终态：创建后至执行中，允许经观察或回调单调推进
NON_TERMINAL_GATEWAY_TASK_STATUSES: frozenset[GatewayTaskStatus] = frozenset(
    {
        GatewayTaskStatus.CREATED,
        GatewayTaskStatus.QUEUED,
        GatewayTaskStatus.WAITING,
        GatewayTaskStatus.RUNNING,
    }
)
