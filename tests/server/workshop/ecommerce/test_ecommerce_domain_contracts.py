from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.contracts.ecommerce import (
    MarketCompetitorBrief,
    validate_ecommerce_deliverable,
)
from app.server.workshop.domain.ecommerce.catalog import get_catalog, upsert_sku
from app.contracts.ecommerce import SkuRecord


def _base_deliverable_fields() -> dict[str, object]:
    """构造交付物基础字段"""
    now = datetime.now(timezone.utc)
    return {
        "project_id": "wp_1",
        "task_id": "task_1",
        "expert_id": "expert_1",
        "created_at": now,
        "source_refs": [],
    }


def test_market_competitor_brief_requires_minimum_fields() -> None:
    """缺最小字段时契约校验失败"""
    with pytest.raises(ValidationError):
        MarketCompetitorBrief.model_validate(
            {**_base_deliverable_fields(), "question": "q"}
        )


def test_validate_ecommerce_deliverable_passes_complete_brief() -> None:
    """弱验收 helper 对完整 payload 通过"""
    payload = {
        **_base_deliverable_fields(),
        "question": "品类机会？",
        "market_scope": "淘天家居",
        "assumptions": ["公开数据"],
        "evidence": [
            {
                "url": "https://example.com",
                "title": "报告",
                "accessed_at": datetime.now(timezone.utc).isoformat(),
            }
        ],
        "competitors": [
            {
                "name": "竞品A",
                "url": "https://shop.example.com",
                "positioning": "高端",
                "price_observation": "199-299",
                "evidence_refs": ["https://example.com"],
            }
        ],
        "findings": ["机会在细分场景"],
        "risks": ["样本有限"],
        "recommended_handoffs": ["ecom_listing_planner_executor"],
    }
    validated = validate_ecommerce_deliverable("MarketCompetitorBrief", payload)
    assert isinstance(validated, MarketCompetitorBrief)


def test_validate_ecommerce_deliverable_unknown_type() -> None:
    """未知交付物类型 fail closed"""
    with pytest.raises(ValueError, match="unknown ecommerce deliverable"):
        validate_ecommerce_deliverable("UnknownType", {})


def test_catalog_helpers_in_brief_json() -> None:
    """catalog 真相存于 brief JSON，无 DB 迁移"""
    brief: dict[str, object] = {}
    record = SkuRecord(project_id="wp_1", sku_id="sku_1", title="测试 SKU")
    brief = upsert_sku(brief, record)
    catalog = get_catalog(brief)
    assert catalog["skus"]["sku_1"]["title"] == "测试 SKU"
