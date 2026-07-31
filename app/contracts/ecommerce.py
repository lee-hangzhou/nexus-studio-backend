from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ECOM_SCHEMA_VERSION = "ecom-v1"
ECOM_FORMULA_VERSION = "ecom-v1"


class EcommerceContract(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)


class EcommerceDeliverableBase(EcommerceContract):
    schema_version: str = ECOM_SCHEMA_VERSION
    project_id: str
    task_id: str
    expert_id: str
    created_at: datetime
    source_refs: list[str] = Field(default_factory=list)


class EvidenceRef(EcommerceContract):
    url: str
    title: str
    accessed_at: datetime


class CompetitorEntry(EcommerceContract):
    name: str
    url: str | None = None
    positioning: str | None = None
    price_observation: str | None = None
    evidence_refs: list[str] = Field(default_factory=list)


class MarketCompetitorBrief(EcommerceDeliverableBase):
    question: str
    market_scope: str
    assumptions: list[str]
    evidence: list[EvidenceRef]
    competitors: list[CompetitorEntry]
    findings: list[str]
    risks: list[str]
    recommended_handoffs: list[str]


class ComplianceCheck(EcommerceContract):
    rule_id: str
    status: str
    message: str


class DetailModule(EcommerceContract):
    type: str
    content: str


class ListingCopyVersion(EcommerceDeliverableBase):
    id: str
    sku_id: str
    version: int = Field(ge=1)
    platform: Literal["taobao", "tmall"]
    locale: str
    title: str
    selling_points: list[str]
    keywords: list[str]
    detail_modules: list[DetailModule]
    compliance_checks: list[ComplianceCheck]
    status: Literal["draft"] = "draft"


class RequiredAsset(EcommerceContract):
    kind: str
    ratio: str | None = None
    count: int = Field(ge=1)


class CreativeBrief(EcommerceDeliverableBase):
    sku_id: str
    objective: str
    placements: list[str]
    audience: str
    messages: list[str]
    required_assets: list[RequiredAsset]
    brand_constraints: list[str]
    prohibited_claims: list[str]
    reference_asset_ids: list[str] = Field(default_factory=list)


class GenerationAssetRef(EcommerceDeliverableBase):
    asset_id: str
    sku_id: str
    generation_task_id: str
    kind: str
    model_id: str
    request_hash: str
    storage_key: str
    version: int = Field(ge=1)
    status: str


class CalendarEntry(EcommerceContract):
    date: str
    activity: str
    owner: str


class KpiTarget(EcommerceContract):
    metric: str
    value: float
    unit: str


class CampaignPlanDoc(EcommerceDeliverableBase):
    name: str
    objective: str
    audience: str
    start_date: str
    end_date: str
    offer: str
    channels: list[str]
    message: str
    calendar: list[CalendarEntry]
    kpi_targets: list[KpiTarget]
    budget_amount_fen: int | None = Field(default=None, ge=0)
    assumptions: list[str]
    dependencies: list[str]


class EmailMessage(EcommerceContract):
    offset: str
    subject: str
    body: str
    cta: str


class EmailSequenceDraft(EcommerceDeliverableBase):
    channel: Literal["email_offsite"] = "email_offsite"
    not_taobao_crm: Literal[True] = True
    audience: str
    trigger: str
    messages: list[EmailMessage]
    stop_conditions: list[str]


class ChannelAllocation(EcommerceContract):
    channel: str
    share_bps: int | None = None
    notes: str | None = None


class CampaignBriefEntry(EcommerceContract):
    name: str
    objective: str
    notes: str | None = None


class AdsStrategyBrief(EcommerceDeliverableBase):
    objective: str
    platform_scope: list[str]
    report_period: str | None = None
    budget_amount_fen: int | None = Field(default=None, ge=0)
    target_kpis: list[KpiTarget]
    channel_allocation: list[ChannelAllocation]
    campaign_briefs: list[CampaignBriefEntry]
    assumptions: list[str]
    freshness_warnings: list[str]


class ComputedKpi(EcommerceContract):
    name: str
    value: float | None
    unit: str
    formula_version: str = ECOM_FORMULA_VERSION
    unavailable_reason: str | None = None


class DiagnosisEntry(EcommerceContract):
    evidence_fields: list[str]
    problem: str


class RecommendedExperiment(EcommerceContract):
    hypothesis: str
    action: str


class AdsReportDiagnosis(EcommerceDeliverableBase):
    report_import_id: str
    period: str
    data_quality_issues: list[str]
    computed_kpis: list[ComputedKpi]
    diagnoses: list[DiagnosisEntry]
    recommended_experiments: list[RecommendedExperiment]


class MetricEntry(EcommerceContract):
    name: str
    value: float | None
    unit: str
    formula_version: str = ECOM_FORMULA_VERSION
    unavailable_reason: str | None = None


class DatasetVersion(EcommerceContract):
    report_type: str
    import_id: str
    row_count: int = Field(ge=0)


class MetricsSnapshot(EcommerceDeliverableBase):
    period: str
    timezone: Literal["Asia/Shanghai"] = "Asia/Shanghai"
    dataset_versions: list[DatasetVersion]
    metrics: list[MetricEntry]
    dimensions: dict[str, Any] = Field(default_factory=dict)
    data_quality_issues: list[str] = Field(default_factory=list)


class ReviewChange(EcommerceContract):
    field: str
    before: str | None = None
    after: str | None = None


class ReviewAnomaly(EcommerceContract):
    metric: str
    description: str


class UnavailableMetric(EcommerceContract):
    name: str
    reason: str


class DailyOrWeeklyReview(EcommerceDeliverableBase):
    period: str
    metrics_snapshot_id: str
    summary: str
    changes: list[ReviewChange]
    anomalies: list[ReviewAnomaly]
    recommendations: list[str]
    unavailable_metrics: list[UnavailableMetric]


class AnomalyItem(EcommerceContract):
    metric: str
    dimension_key: str
    observed_value: float | None
    unit: str
    rule: str
    threshold: float | None = None
    severity: str
    evidence_ref: str


class AnomalyList(EcommerceDeliverableBase):
    period: str
    items: list[AnomalyItem]


class ChartSeries(EcommerceContract):
    name: str
    points: list[float | None]


class ChartArtifact(EcommerceDeliverableBase):
    chart_id: str
    metrics_snapshot_id: str
    chart_type: str
    series: list[ChartSeries]
    unit: str
    storage_key: str


class PublishChange(EcommerceContract):
    path: str
    before: Any | None = None
    after: Any | None = None


class PublishDiff(EcommerceDeliverableBase):
    operation: Literal["publish", "edit", "list", "unlist"]
    shop_connection_id: str
    item_id: str | None = None
    sku_ids: list[str]
    base_version: str | None = None
    target_version: str
    changes: list[PublishChange]
    warnings: list[str] = Field(default_factory=list)
    idempotency_key: str
    payload_hash: str
    expires_at: datetime


class PublishReceipt(EcommerceDeliverableBase):
    operation: Literal["publish", "edit", "list", "unlist"]
    shop_connection_id: str
    platform_item_id: str
    sku_ids: list[str]
    request_id: str
    idempotency_key: str
    payload_hash: str
    status: str
    executed_at: datetime
    platform_response_code: str
    audit_log_ref: str


class SkuRecord(EcommerceContract):
    schema_version: str = ECOM_SCHEMA_VERSION
    project_id: str
    sku_id: str
    item_id: str | None = None
    title: str | None = None
    platform: Literal["taobao", "tmall"] | None = None
    status: str | None = None


class SkuCostRecord(EcommerceContract):
    schema_version: str = ECOM_SCHEMA_VERSION
    project_id: str
    sku_id: str
    effective_from: datetime
    currency: Literal["CNY"] = "CNY"
    purchase_cost_fen: int = Field(ge=0)
    fulfillment_cost_fen: int = Field(ge=0)
    platform_fee_rate_bps: int = Field(ge=0)
    other_variable_cost_fen: int = Field(ge=0)


class InventoryLevel(EcommerceContract):
    schema_version: str = ECOM_SCHEMA_VERSION
    project_id: str
    sku_id: str
    item_id: str
    available_qty: int = Field(ge=0)
    locked_qty: int = Field(ge=0)
    snapshot_at: datetime


class OrderLineRef(EcommerceContract):
    schema_version: str = ECOM_SCHEMA_VERSION
    project_id: str
    order_id: str
    item_id: str
    sku_id: str
    created_at: datetime


class ProductAssetRef(EcommerceContract):
    schema_version: str = ECOM_SCHEMA_VERSION
    project_id: str
    asset_id: str
    sku_id: str
    kind: str
    storage_key: str
    version: int = Field(ge=1)


class CopyVersionRef(EcommerceContract):
    schema_version: str = ECOM_SCHEMA_VERSION
    project_id: str
    copy_version_id: str
    sku_id: str
    version: int = Field(ge=1)


class TaobaoOrderLineNormalized(EcommerceContract):
    shop_id: str
    order_id: str
    item_id: str
    sku_id: str
    created_at: datetime
    quantity: int = Field(ge=0)
    item_amount_fen: int
    discount_amount_fen: int = 0
    shipping_amount_fen: int = 0
    refund_amount_fen: int = 0
    order_status: str
    currency: Literal["CNY"] = "CNY"
    paid_at: datetime | None = None
    completed_at: datetime | None = None
    buyer_region: str | None = None


class TaobaoProductDailyNormalized(EcommerceContract):
    shop_id: str
    stat_date: str
    item_id: str
    sku_id: str | None = None
    impressions: int = Field(ge=0)
    visitors: int = Field(ge=0)
    detail_views: int = Field(ge=0)
    add_to_cart_users: int = Field(ge=0)
    paid_buyers: int = Field(ge=0)
    paid_orders: int = Field(ge=0)
    paid_quantity: int = Field(ge=0)
    paid_amount_fen: int = Field(ge=0)
    refund_amount_fen: int = Field(ge=0)
    favorites: int | None = Field(default=None, ge=0)
    search_visitors: int | None = Field(default=None, ge=0)


class TaobaoInventorySnapshotNormalized(EcommerceContract):
    shop_id: str
    snapshot_at: datetime
    item_id: str
    sku_id: str
    available_qty: int = Field(ge=0)
    locked_qty: int = Field(ge=0)
    inbound_qty: int | None = Field(default=None, ge=0)
    warehouse_id: str | None = None


class TaobaoAdsDailyNormalized(EcommerceContract):
    """万相台日报：金额字段 UNIT_UNVERIFIED，只存 raw_string，禁止自动 fen"""

    shop_id: str
    stat_date: str
    campaign_id: str
    ad_group_id: str | None = None
    impressions: int = Field(ge=0)
    clicks: int = Field(ge=0)
    attributed_orders: int = Field(ge=0)
    campaign_name: str | None = None
    ad_group_name: str | None = None
    spend_raw: str | None = None
    revenue_raw: str | None = None
    unit_status: Literal["UNIT_UNVERIFIED"] | None = None
    official_roi: str | None = None
    official_ctr: str | None = None
    official_cvr: str | None = None


class ShopConnectionPublic(EcommerceContract):
    """对外店铺连接视图：无 Token/secret"""

    shop_connection_id: str
    status: Literal["connected", "REAUTH_REQUIRED", "disconnected", "unsupported"]
    app_type: Literal["self_dev", "subscription_isv"] | None = None
    expires_at: datetime | None = None
    semantic_permissions: list[str] = Field(default_factory=list)


class MetricAvailability(EcommerceContract):
    """指标可用性标记（前端不得把缺失显示为 0）"""

    name: str
    status: Literal["ok", "unavailable", "unsupported", "unit_unverified", "reauth_required"]
    reason: str | None = None
    raw_value: str | None = None


ECOMMERCE_DELIVERABLE_TYPES: dict[str, type[EcommerceContract]] = {
    "MarketCompetitorBrief": MarketCompetitorBrief,
    "ListingCopyVersion": ListingCopyVersion,
    "CreativeBrief": CreativeBrief,
    "GenerationAssetRef": GenerationAssetRef,
    "CampaignPlanDoc": CampaignPlanDoc,
    "EmailSequenceDraft": EmailSequenceDraft,
    "AdsStrategyBrief": AdsStrategyBrief,
    "AdsReportDiagnosis": AdsReportDiagnosis,
    "MetricsSnapshot": MetricsSnapshot,
    "DailyOrWeeklyReview": DailyOrWeeklyReview,
    "AnomalyList": AnomalyList,
    "ChartArtifact": ChartArtifact,
    "PublishDiff": PublishDiff,
    "PublishReceipt": PublishReceipt,
}


def validate_ecommerce_deliverable(type_name: str, payload: dict[str, Any]) -> EcommerceContract:
    """弱验收：缺最小字段则 ValidationError"""
    model = ECOMMERCE_DELIVERABLE_TYPES.get(type_name)
    if model is None:
        raise ValueError(f"unknown ecommerce deliverable type: {type_name}")
    result: EcommerceContract = model.model_validate(payload)
    return result
