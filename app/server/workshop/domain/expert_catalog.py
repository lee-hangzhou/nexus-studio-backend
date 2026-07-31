from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Tuple

from app.server.workshop.domain.ecommerce.profiles import ECOM_PROFILES, get_ecom_profile

EXPERT_CATALOG_KIND_EXPERT: Literal["expert"] = "expert"
EXPERT_CATALOG_KIND_TEAM: Literal["team"] = "team"
ExpertCatalogKind = Literal["expert", "team"]

_ECOM_ROLE_PHRASES: dict[str, str] = {
    "ecom_market_competitor_advisor": "市场与竞品研究",
    "ecom_listing_planner_executor": "商品策划与文案",
    "ecom_campaign_planner_executor": "营销活动策划",
    "ecom_ads_strategy_analyzer_executor": "广告策略与分析",
    "ecom_ops_analytics_executor": "经营分析与复盘",
    "ecom_taobao_store_ops_executor": "淘天店铺运营",
}

_ECOM_TAGS: dict[str, Tuple[str, ...]] = {
    "ecom_market_competitor_advisor": ("市场", "竞品", "调研"),
    "ecom_listing_planner_executor": ("商品", "文案", "Listing"),
    "ecom_campaign_planner_executor": ("营销", "活动", "策划"),
    "ecom_ads_strategy_analyzer_executor": ("广告", "投放", "分析"),
    "ecom_ops_analytics_executor": ("经营", "复盘", "指标"),
    "ecom_taobao_store_ops_executor": ("店铺", "运营", "淘天"),
}

_ECOM_APPLICABLE_TASKS: dict[str, Tuple[str, ...]] = {
    "ecom_market_competitor_advisor": ("market_research", "competitor_analysis"),
    "ecom_listing_planner_executor": ("listing_copy", "creative_brief"),
    "ecom_campaign_planner_executor": ("campaign_plan", "email_sequence"),
    "ecom_ads_strategy_analyzer_executor": ("ads_strategy", "ads_report"),
    "ecom_ops_analytics_executor": ("metrics_review", "anomaly_scan"),
    "ecom_taobao_store_ops_executor": ("store_ops", "publish_diff"),
}


@dataclass(frozen=True, slots=True)
class ExpertCatalogEntry:
    """用户可见专家目录项"""

    key: str
    name: str
    role_phrase: str
    tags: Tuple[str, ...]
    scenes: Tuple[str, ...]
    avatar_id: str
    kind: ExpertCatalogKind
    applicable_tasks: Tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ExpertTeamCatalogEntry:
    """用户可见专家团队目录项"""

    key: str
    name: str
    role_phrase: str
    tags: Tuple[str, ...]
    scenes: Tuple[str, ...]
    avatar_id: str
    kind: ExpertCatalogKind
    member_keys: Tuple[str, ...]
    applicable_tasks: Tuple[str, ...] = ()


_AVATAR_SLUGS: dict[str, str] = {
    "host": "host",
    "ecom_market_competitor_advisor": "ecom-market",
    "ecom_listing_planner_executor": "ecom-listing",
    "ecom_campaign_planner_executor": "ecom-campaign",
    "ecom_ads_strategy_analyzer_executor": "ecom-ads",
    "ecom_ops_analytics_executor": "ecom-ops",
    "ecom_taobao_store_ops_executor": "ecom-store",
}


def _avatar_id(key: str) -> str:
    """按专家 key 派生头像 id"""
    return _AVATAR_SLUGS.get(key, key.replace("_", "-"))


def _build_ecom_entry(profile_key: str) -> ExpertCatalogEntry:
    """组装电商业务专家目录项"""
    profile = get_ecom_profile(profile_key)
    tags = _ECOM_TAGS[profile.key]
    return ExpertCatalogEntry(
        key=profile.key,
        name=profile.name,
        role_phrase=_ECOM_ROLE_PHRASES[profile.key],
        tags=tags,
        scenes=tags,
        avatar_id=_avatar_id(profile.key),
        kind=EXPERT_CATALOG_KIND_EXPERT,
        applicable_tasks=_ECOM_APPLICABLE_TASKS[profile.key],
    )


_HOST_ENTRY = ExpertCatalogEntry(
    key="host",
    name="项目助手",
    role_phrase="梳理目标并协调协作",
    tags=("协调", "规划", "调度"),
    scenes=("协调", "规划", "调度"),
    avatar_id=_avatar_id("host"),
    kind=EXPERT_CATALOG_KIND_EXPERT,
    applicable_tasks=("host",),
)

_EXPERT_DIRECTORY: tuple[ExpertCatalogEntry, ...] = (
    _HOST_ENTRY,
    *(_build_ecom_entry(p.key) for p in ECOM_PROFILES),
)

_EXPERT_BY_KEY: dict[str, ExpertCatalogEntry] = {entry.key: entry for entry in _EXPERT_DIRECTORY}

_EXPERT_TEAMS: tuple[ExpertTeamCatalogEntry, ...] = ()

_TEAM_BY_KEY: dict[str, ExpertTeamCatalogEntry] = {}


def list_expert_directory() -> tuple[ExpertCatalogEntry, ...]:
    """返回产品专家目录（含 host 与六席业务专家）"""
    return _EXPERT_DIRECTORY


def list_expert_teams() -> tuple[ExpertTeamCatalogEntry, ...]:
    """返回专家团队目录（已停用，恒为空）"""
    return _EXPERT_TEAMS


def get_catalog_entry(key: str) -> ExpertCatalogEntry:
    """按 key 读取专家目录项"""
    try:
        return _EXPERT_BY_KEY[key]
    except KeyError as exc:
        raise KeyError(key) from exc


def get_team(key: str) -> ExpertTeamCatalogEntry:
    """按 key 读取团队目录项"""
    try:
        return _TEAM_BY_KEY[key]
    except KeyError as exc:
        raise KeyError(key) from exc


def is_team_key(key: str) -> bool:
    """是否为团队 key"""
    return False


def resolve_catalog_key(key: str) -> ExpertCatalogEntry | ExpertTeamCatalogEntry:
    """解析专家或团队 key"""
    return get_catalog_entry(key)
