from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any

from app.contracts.ecommerce import (
    CopyVersionRef,
    InventoryLevel,
    OrderLineRef,
    ProductAssetRef,
    SkuCostRecord,
    SkuRecord,
)

CATALOG_BRIEF_KEY = "catalog"


def _catalog_section(brief: dict[str, Any]) -> dict[str, Any]:
    """读取 brief 中电商目录分段"""
    raw = brief.get(CATALOG_BRIEF_KEY)
    if not isinstance(raw, dict):
        return {}
    return raw


def get_catalog(brief: dict[str, Any]) -> dict[str, Any]:
    """读取项目 brief 中的电商目录真相（纯 JSON）"""
    section = _catalog_section(brief)
    return {
        "skus": deepcopy(section.get("skus", {})),
        "costs": deepcopy(section.get("costs", [])),
        "inventory": deepcopy(section.get("inventory", [])),
        "order_lines": deepcopy(section.get("order_lines", [])),
        "assets": deepcopy(section.get("assets", [])),
        "copy_versions": deepcopy(section.get("copy_versions", [])),
    }


def upsert_sku(brief: dict[str, Any], record: SkuRecord) -> dict[str, Any]:
    """写入或更新 SKU 记录，返回新 brief"""
    updated = deepcopy(brief)
    section = updated.setdefault(CATALOG_BRIEF_KEY, {})
    skus = section.setdefault("skus", {})
    skus[record.sku_id] = record.model_dump(mode="json")
    return updated


def upsert_sku_cost(brief: dict[str, Any], record: SkuCostRecord) -> dict[str, Any]:
    """追加有效日期 SKU 成本记录"""
    updated = deepcopy(brief)
    section = updated.setdefault(CATALOG_BRIEF_KEY, {})
    costs = section.setdefault("costs", [])
    costs.append(record.model_dump(mode="json"))
    return updated


def upsert_inventory(brief: dict[str, Any], record: InventoryLevel) -> dict[str, Any]:
    """写入库存快照"""
    updated = deepcopy(brief)
    section = updated.setdefault(CATALOG_BRIEF_KEY, {})
    inventory = section.setdefault("inventory", [])
    inventory.append(record.model_dump(mode="json"))
    return updated


def upsert_order_line(brief: dict[str, Any], record: OrderLineRef) -> dict[str, Any]:
    """写入订单行引用"""
    updated = deepcopy(brief)
    section = updated.setdefault(CATALOG_BRIEF_KEY, {})
    order_lines = section.setdefault("order_lines", [])
    order_lines.append(record.model_dump(mode="json"))
    return updated


def upsert_product_asset(brief: dict[str, Any], record: ProductAssetRef) -> dict[str, Any]:
    """写入产品资产引用"""
    updated = deepcopy(brief)
    section = updated.setdefault(CATALOG_BRIEF_KEY, {})
    assets = section.setdefault("assets", [])
    assets.append(record.model_dump(mode="json"))
    return updated


def upsert_copy_version(brief: dict[str, Any], record: CopyVersionRef) -> dict[str, Any]:
    """写入文案版本引用"""
    updated = deepcopy(brief)
    section = updated.setdefault(CATALOG_BRIEF_KEY, {})
    copy_versions = section.setdefault("copy_versions", [])
    copy_versions.append(record.model_dump(mode="json"))
    return updated


def effective_sku_cost(
    brief: dict[str, Any],
    *,
    sku_id: str,
    as_of: datetime,
) -> SkuCostRecord | None:
    """按 effective_from 取最近一条有效成本"""
    section = _catalog_section(brief)
    costs_raw = section.get("costs", [])
    if not isinstance(costs_raw, list):
        return None
    candidates: list[SkuCostRecord] = []
    for item in costs_raw:
        if not isinstance(item, dict):
            continue
        record = SkuCostRecord.model_validate(item)
        if record.sku_id != sku_id:
            continue
        effective_from = record.effective_from
        if effective_from.tzinfo is None:
            effective_from = effective_from.replace(tzinfo=as_of.tzinfo)
        if effective_from <= as_of:
            candidates.append(record)
    if not candidates:
        return None
    return max(candidates, key=lambda row: row.effective_from)
