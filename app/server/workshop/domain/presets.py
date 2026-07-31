from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from app.server.workshop.domain.ecommerce.profiles import (
    ECOM_PROFILES,
    ECOM_PRESET_KEYS,
    get_ecom_profile,
    is_ecom_preset,
)
from app.server.workshop.domain.enums import WorkshopExpertKind


@dataclass(frozen=True, slots=True)
class PresetExpert:
    """预置专家定义"""

    key: str
    name: str
    kind: WorkshopExpertKind


ECOM_PRESET_EXPERTS: tuple[PresetExpert, ...] = tuple(
    PresetExpert(profile.key, profile.name, profile.kind) for profile in ECOM_PROFILES
)

PRODUCT_EXPERT_KEYS: frozenset[str] = frozenset(ECOM_PRESET_KEYS)


def presets_for_keys(keys: Sequence[str]) -> tuple[PresetExpert, ...]:
    """按业务专家 key 列表解析预置名册"""
    return tuple(get_preset(key) for key in keys)


def get_preset(key: str) -> PresetExpert:
    """按 key 读取预置业务专家；不存在则抛 KeyError"""
    if is_ecom_preset(key):
        profile = get_ecom_profile(key)
        return PresetExpert(profile.key, profile.name, profile.kind)
    raise KeyError(key)


def list_invite_directory() -> tuple[PresetExpert, ...]:
    """可邀请专家目录：仅产品业务专家"""
    return ECOM_PRESET_EXPERTS
