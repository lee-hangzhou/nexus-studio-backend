from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.contracts.ecommerce import TaobaoOrderLineNormalized
from app.server.workshop.domain.ecommerce.report_import import (
    UNSUPPORTED_REPORT_TEMPLATE,
    NormalizedReportType,
    import_normalized_rows,
    import_raw_template,
)


def test_raw_template_import_returns_unsupported_report_template() -> None:
    """未验证原始模板 fail-closed"""
    result = import_raw_template(
        template_id="shengyi_order_v2024",
        raw_rows=[{"col_a": "1"}],
    )
    assert result.status == "rejected"
    assert result.accepted_rows == 0
    assert result.errors[0].code == UNSUPPORTED_REPORT_TEMPLATE


def test_normalized_import_accepts_valid_rows() -> None:
    """归一化行校验通过"""
    rows = [
        {
            "shop_id": "shop_1",
            "order_id": "o1",
            "item_id": "i1",
            "sku_id": "sku_1",
            "created_at": "2026-07-01T10:00:00+08:00",
            "quantity": 2,
            "item_amount_fen": 10000,
            "discount_amount_fen": 0,
            "shipping_amount_fen": 500,
            "refund_amount_fen": 0,
            "order_status": "paid",
        }
    ]
    result = import_normalized_rows(NormalizedReportType.TAOBAO_ORDER_LINE, rows)
    assert result.status == "accepted"
    assert result.accepted_rows == 1
    assert isinstance(result.rows[0], TaobaoOrderLineNormalized)


def test_normalized_import_rejects_missing_required_field() -> None:
    """缺必填字段 rejected"""
    rows = [{"shop_id": "shop_1", "order_id": "o1"}]
    result = import_normalized_rows(NormalizedReportType.TAOBAO_ORDER_LINE, rows)
    assert result.status == "rejected"
    assert result.accepted_rows == 0
    assert result.errors[0].code == "validation_error"


def test_normalized_import_rejects_duplicate_natural_key() -> None:
    """重复自然键 partially/rejected"""
    row = {
        "shop_id": "shop_1",
        "order_id": "o1",
        "item_id": "i1",
        "sku_id": "sku_1",
        "created_at": datetime(2026, 7, 1, 10, 0, tzinfo=timezone.utc).isoformat(),
        "quantity": 1,
        "item_amount_fen": 100,
        "discount_amount_fen": 0,
        "shipping_amount_fen": 0,
        "refund_amount_fen": 0,
        "order_status": "paid",
    }
    result = import_normalized_rows(
        NormalizedReportType.TAOBAO_ORDER_LINE, [row, dict(row)]
    )
    assert result.status == "partially_accepted"
    assert result.accepted_rows == 1
    assert any(err.code == "duplicate_natural_key" for err in result.errors)
