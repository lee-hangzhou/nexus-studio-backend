from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Sequence, Type

from pydantic import ValidationError

from app.contracts.ecommerce import (
    TaobaoAdsDailyNormalized,
    TaobaoInventorySnapshotNormalized,
    TaobaoOrderLineNormalized,
    TaobaoProductDailyNormalized,
)

UNSUPPORTED_REPORT_TEMPLATE = "UNSUPPORTED_REPORT_TEMPLATE"


class NormalizedReportType(str, Enum):
    TAOBAO_ORDER_LINE = "taobao_order_line"
    TAOBAO_PRODUCT_DAILY = "taobao_product_daily"
    TAOBAO_INVENTORY_SNAPSHOT = "taobao_inventory_snapshot"
    TAOBAO_ADS_DAILY = "taobao_ads_daily"


_REPORT_MODELS: dict[NormalizedReportType, Type[Any]] = {
    NormalizedReportType.TAOBAO_ORDER_LINE: TaobaoOrderLineNormalized,
    NormalizedReportType.TAOBAO_PRODUCT_DAILY: TaobaoProductDailyNormalized,
    NormalizedReportType.TAOBAO_INVENTORY_SNAPSHOT: TaobaoInventorySnapshotNormalized,
    NormalizedReportType.TAOBAO_ADS_DAILY: TaobaoAdsDailyNormalized,
}


@dataclass(frozen=True, slots=True)
class RowImportError:
    row_index: int
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class ImportResult:
    report_type: NormalizedReportType | None
    status: str
    accepted_rows: int
    rejected_rows: int
    rows: tuple[Any, ...]
    errors: tuple[RowImportError, ...]


def import_normalized_rows(
    report_type: NormalizedReportType,
    rows: Sequence[dict[str, Any]],
) -> ImportResult:
    """导入已归一化行；逐行校验，缺字段/非法值 fail closed"""
    model = _REPORT_MODELS[report_type]
    accepted: list[Any] = []
    errors: list[RowImportError] = []
    seen_keys: set[str] = set()
    for index, raw in enumerate(rows):
        try:
            row = model.model_validate(raw)
        except ValidationError as exc:
            errors.append(
                RowImportError(
                    row_index=index,
                    code="validation_error",
                    message=str(exc.errors()[0]["msg"]),
                )
            )
            continue
        natural_key = _natural_key(report_type, row)
        if natural_key in seen_keys:
            errors.append(
                RowImportError(
                    row_index=index,
                    code="duplicate_natural_key",
                    message=f"duplicate key: {natural_key}",
                )
            )
            continue
        seen_keys.add(natural_key)
        accepted.append(row)
    if not accepted and errors:
        status = "rejected"
    elif errors:
        status = "partially_accepted"
    else:
        status = "accepted"
    return ImportResult(
        report_type=report_type,
        status=status,
        accepted_rows=len(accepted),
        rejected_rows=len(errors),
        rows=tuple(accepted),
        errors=tuple(errors),
    )


def import_raw_template(
    *,
    template_id: str,
    raw_rows: Sequence[dict[str, Any]],
) -> ImportResult:
    """原始淘天模板导入：T6 未验证前一律 UNSUPPORTED_REPORT_TEMPLATE"""
    del template_id, raw_rows
    return ImportResult(
        report_type=None,
        status="rejected",
        accepted_rows=0,
        rejected_rows=0,
        rows=(),
        errors=(
            RowImportError(
                row_index=0,
                code=UNSUPPORTED_REPORT_TEMPLATE,
                message="raw taobao report template mapping not enabled",
            ),
        ),
    )


def _natural_key(report_type: NormalizedReportType, row: Any) -> str:
    """构造稳定自然键"""
    if report_type is NormalizedReportType.TAOBAO_ORDER_LINE:
        return f"{row.order_id}:{row.sku_id}:{row.created_at.isoformat()}"
    if report_type is NormalizedReportType.TAOBAO_PRODUCT_DAILY:
        sku = row.sku_id or "_"
        return f"{row.stat_date}:{row.item_id}:{sku}"
    if report_type is NormalizedReportType.TAOBAO_INVENTORY_SNAPSHOT:
        return f"{row.snapshot_at.isoformat()}:{row.sku_id}"
    if report_type is NormalizedReportType.TAOBAO_ADS_DAILY:
        group = row.ad_group_id or "_"
        return f"{row.stat_date}:{row.campaign_id}:{group}"
    raise ValueError(f"unsupported report type: {report_type}")
