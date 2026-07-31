from __future__ import annotations

import pytest

from app.server.workshop.domain.expert_catalog import (
    EXPERT_CATALOG_KIND_EXPERT,
    get_catalog_entry,
    is_team_key,
    list_expert_directory,
    list_expert_teams,
)
from app.server.workshop.domain.ecommerce.profiles import get_ecom_profile
from app.server.workshop.domain.presets import PRODUCT_EXPERT_KEYS


def test_catalog_is_host_plus_business_experts_only() -> None:
    """目录仅 host 与业务专家，不含工具壳与团队"""
    entries = list_expert_directory()
    keys = {entry.key for entry in entries}
    assert "host" in keys
    assert keys >= PRODUCT_EXPERT_KEYS
    assert "research_advisor" not in keys
    assert "general_executor" not in keys
    assert "browser_executor" not in keys
    assert len(entries) == 1 + len(PRODUCT_EXPERT_KEYS)


def test_catalog_role_phrase_has_no_implementation_constraints() -> None:
    """role_phrase 不含实现约束措辞"""
    forbidden = ("缺失值", "不显示为0", "MCP", "unsupported", "blocked", "Schema")
    for entry in list_expert_directory():
        phrase = entry.role_phrase
        for token in forbidden:
            assert token not in phrase, f"{entry.key} role_phrase contains {token}"


def test_catalog_entries_have_tags_avatar() -> None:
    """每条专家目录含 tags、avatar_id；展示名不含「顾问」「执行」后缀"""
    for entry in list_expert_directory():
        if entry.key == "host":
            continue
        assert 2 <= len(entry.tags) <= 3
        assert entry.avatar_id
        assert entry.kind == EXPERT_CATALOG_KIND_EXPERT
        assert "顾问" not in entry.name
        assert "执行" not in entry.name


def test_ecom_profile_names_match_catalog() -> None:
    """电商 profile 展示名与目录一致，且不含「顾问」「执行」后缀"""
    expected = {
        "ecom_market_competitor_advisor": "市场与竞品研究",
        "ecom_listing_planner_executor": "商品策划与文案",
        "ecom_campaign_planner_executor": "营销活动策划",
        "ecom_ads_strategy_analyzer_executor": "广告策略与分析",
        "ecom_ops_analytics_executor": "经营分析与复盘",
        "ecom_taobao_store_ops_executor": "淘天店铺运营",
    }
    for key, name in expected.items():
        assert get_ecom_profile(key).name == name
        assert get_catalog_entry(key).name == name


def test_expert_teams_removed_from_product() -> None:
    """产品不再提供专家团队目录"""
    assert list_expert_teams() == ()
    assert not is_team_key("taobao_ops_team")
    assert not is_team_key("general_collab_team")


def test_unknown_catalog_key_raises() -> None:
    """未知 key 抛 KeyError"""
    with pytest.raises(KeyError):
        get_catalog_entry("not_a_real_expert")
