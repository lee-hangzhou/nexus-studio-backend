from __future__ import annotations

from app.server.workshop.domain.presets import PRODUCT_EXPERT_KEYS, get_preset, presets_for_keys


def test_product_directory_keys_are_business_experts_only() -> None:
    """产品目录仅业务专家，不含工具型专家与旧通用顾问壳"""
    forbidden = {
        "research_advisor",
        "writing_advisor",
        "data_advisor",
        "general_executor",
        "browser_executor",
        "code_executor",
        "taobao_ops_team",
        "general_collab_team",
    }
    assert PRODUCT_EXPERT_KEYS.isdisjoint(forbidden)
    assert "ecom_listing_planner_executor" in PRODUCT_EXPERT_KEYS


def test_presets_for_keys_seeds_only_requested() -> None:
    """名册 bootstrap 按 initial_expert_keys，不按 pack"""
    presets = presets_for_keys(("ecom_ops_analytics_executor",))
    assert len(presets) == 1
    assert presets[0].key == "ecom_ops_analytics_executor"


def test_business_expert_display_names_have_no_advisor_executor_suffix() -> None:
    """产品展示名不以「顾问」「执行」结尾，也不含「执行专家」"""
    for key in PRODUCT_EXPERT_KEYS:
        name = get_preset(key).name
        assert not name.endswith("顾问")
        assert not name.endswith("执行")
        assert "执行专家" not in name
        assert "顾问" not in name or key.startswith("never")
