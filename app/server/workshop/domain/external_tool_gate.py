from __future__ import annotations

from typing import FrozenSet

from app.server.workshop.domain.enums import WorkshopToolCapability

_EXTERNAL_CAPABILITIES = frozenset(
    {
        WorkshopToolCapability.BROWSER_WRITE,
        WorkshopToolCapability.MCP,
        WorkshopToolCapability.CREATE_SCHEDULE,
        WorkshopToolCapability.TAOBAO_STORE_WRITE,
        WorkshopToolCapability.GENERATION_SUBMIT,
    }
)


def is_external_capability(capability: WorkshopToolCapability) -> bool:
    """是否属于需任务级弹窗授权的外部副作用"""
    return capability in _EXTERNAL_CAPABILITIES


def executor_may_use_external(
    granted: FrozenSet[WorkshopToolCapability],
    capability: WorkshopToolCapability,
) -> bool:
    """非外部能力直接放行；外部能力必须已出现在 granted 授权集合中"""
    if not is_external_capability(capability):
        return True
    return capability in granted
