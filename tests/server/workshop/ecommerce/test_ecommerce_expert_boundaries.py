from __future__ import annotations

from app.server.workshop.domain.ecommerce.profiles import ECOM_PROFILES, get_ecom_profile
from app.server.workshop.domain.enums import WorkshopToolCapability


def test_six_experts_present_with_distinct_boundaries() -> None:
    """覆盖 six experts present with distinct boundaries"""
    assert len(ECOM_PROFILES) == 6
    boundaries = {p.key: p.system_prompt_boundary for p in ECOM_PROFILES}
    assert "竞品后台 GMV" in boundaries["ecom_market_competitor_advisor"]
    assert "Schema" in boundaries["ecom_listing_planner_executor"]
    assert "不自动改价" in boundaries["ecom_campaign_planner_executor"]
    assert "UNIT_UNVERIFIED" in boundaries["ecom_ads_strategy_analyzer_executor"]
    assert "官方导出" in boundaries["ecom_ops_analytics_executor"]
    assert "upshelf/downshelf" in boundaries["ecom_taobao_store_ops_executor"]


def test_ads_expert_has_no_store_write() -> None:
    """覆盖 ads expert has no store write"""
    ads = get_ecom_profile("ecom_ads_strategy_analyzer_executor")
    assert WorkshopToolCapability.TAOBAO_STORE_WRITE not in ads.capability_allowlist


def test_store_ops_requires_taobao_write_capability() -> None:
    """覆盖 store ops requires taobao write capability"""
    store = get_ecom_profile("ecom_taobao_store_ops_executor")
    assert WorkshopToolCapability.TAOBAO_STORE_WRITE in store.capability_allowlist
