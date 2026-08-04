"""工作流定义校验所需的 preset 目录与 allowlist。"""

from __future__ import annotations

from app.server.workshop.domain.ecommerce.profiles import ECOM_PROFILES
from app.server.workshop.domain.enums import WorkshopToolCapability


def workshop_preset_catalog() -> tuple[
    frozenset[str], dict[str, frozenset[WorkshopToolCapability]]
]:
    """返回已知 preset_key 与能力白名单"""
    keys: set[str] = set()
    allowlists: dict[str, frozenset[WorkshopToolCapability]] = {}
    for profile in ECOM_PROFILES:
        keys.add(profile.key)
        allowlists[profile.key] = frozenset(profile.capability_allowlist)
    return frozenset(keys), allowlists
