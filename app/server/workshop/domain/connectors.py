from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True, slots=True)
class ConnectorDef:
    key: str
    name: str
    description: str


# 连接器 = 外部账户授权；仅淘宝店铺有真实 OAuth 路径
PLATFORM_CONNECTORS: tuple[ConnectorDef, ...] = (
    ConnectorDef(
        key="taobao_shop",
        name="淘宝店铺",
        description="店铺授权连接；写入仍需逐次确认",
    ),
)


def default_connector_statuses() -> dict[str, str]:
    """返回连接器默认状态表"""
    return {c.key: "disconnected" for c in PLATFORM_CONNECTORS}


def _normalize_status(status: str) -> str:
    """规范化连接器状态值"""
    if status == "REAUTH_REQUIRED":
        return "reauth_required"
    return status


def merge_connector_statuses(
    source_statuses: Mapping[str, str],
) -> dict[str, str]:
    """将 data_sources / shop 键映射到 connector 键"""
    out = default_connector_statuses()
    alias = {
        "shop": "taobao_shop",
        "taobao_shop": "taobao_shop",
    }
    for raw_key, status in source_statuses.items():
        mapped = alias.get(raw_key, raw_key)
        if mapped in out:
            out[mapped] = _normalize_status(status)
    return out
