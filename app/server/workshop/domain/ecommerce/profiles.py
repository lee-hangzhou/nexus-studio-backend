from __future__ import annotations

from dataclasses import dataclass
from typing import FrozenSet, Tuple

from app.server.workshop.domain.enums import WorkshopExpertKind, WorkshopToolCapability


@dataclass(frozen=True, slots=True)
class ExpertProfile:
    """电商专家包 Profile（平台模板）"""

    key: str
    name: str
    kind: WorkshopExpertKind
    skill_refs: Tuple[str, ...]
    capability_allowlist: FrozenSet[WorkshopToolCapability]
    deliverable_types: Tuple[str, ...]
    mcp_server_ids: Tuple[str, ...] = ()
    beta: bool = False
    system_prompt_boundary: str = ""


def allowlist_fingerprint(allowlist: FrozenSet[WorkshopToolCapability]) -> str:
    """对能力集合做稳定指纹（sha256 hex）"""
    import hashlib

    payload = ",".join(sorted(cap.value for cap in allowlist))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


_READ = frozenset(
    {
        WorkshopToolCapability.WEB_SEARCH,
        WorkshopToolCapability.WEB_FETCH_READONLY,
        WorkshopToolCapability.READ_UPLOADS,
        WorkshopToolCapability.READ_PROJECT_FILES,
        WorkshopToolCapability.BROWSER_READ,
        WorkshopToolCapability.PROPOSE_INVITE,
    }
)

ECOM_PROFILES: tuple[ExpertProfile, ...] = (
    ExpertProfile(
        key="ecom_market_competitor_advisor",
        name="市场与竞品研究",
        kind=WorkshopExpertKind.ADVISOR,
        skill_refs=(
            "customer-research",
            "competitive-brief",
            "competitor-profiling",
            "ecommerce-competitor-analysis",
        ),
        capability_allowlist=_READ,
        deliverable_types=("MarketCompetitorBrief",),
        system_prompt_boundary=(
            "只输出有来源的结论；禁止声称已连接生意参谋/选品 MCP；"
            "禁止提供未授权的销量数字；禁止宣称可获得竞品后台 GMV/访客；"
            "缺数据源时返回 unavailable/unsupported，不伪造。"
        ),
    ),
    ExpertProfile(
        key="ecom_listing_planner_executor",
        name="商品策划与文案",
        kind=WorkshopExpertKind.EXECUTOR,
        skill_refs=(
            "product-description-generator",
            "cro",
            "ecom-landing-pages",
            "ad-creative",
            "taobao-listing-official-rules",
        ),
        capability_allowlist=frozenset(
            _READ
            | {
                WorkshopToolCapability.WRITE_PROJECT_FILES,
                WorkshopToolCapability.WRITE_TEMP_WORKSPACE,
                WorkshopToolCapability.GENERATION_LIST_MODELS,
                WorkshopToolCapability.GENERATION_SUBMIT,
                WorkshopToolCapability.SANDBOX_EXECUTE,
                WorkshopToolCapability.REQUEST_EXTERNAL_AUTH,
            }
        ),
        deliverable_types=(
            "ListingCopyVersion",
            "CreativeBrief",
            "GenerationAssetRef",
        ),
        system_prompt_boundary=(
            "标题/合规限制仅来自运行时 publish/edit Schema，禁止硬编码长度；"
            "使用 Schema、图片、SKU、发布编辑能力描述交付；无事实不写医疗功效；"
            "不调用店铺发布 Tool；缺 Schema 时 unavailable，不伪造。"
        ),
    ),
    ExpertProfile(
        key="ecom_campaign_planner_executor",
        name="营销活动策划",
        kind=WorkshopExpertKind.EXECUTOR,
        skill_refs=(
            "campaign-plan",
            "ecommerce-marketing-strategy-builder",
            "email-sequence",
        ),
        capability_allowlist=frozenset(
            _READ
            | {
                WorkshopToolCapability.WRITE_PROJECT_FILES,
                WorkshopToolCapability.WRITE_TEMP_WORKSPACE,
            }
        ),
        deliverable_types=("CampaignPlanDoc", "EmailSequenceDraft"),
        system_prompt_boundary=(
            "默认只输出策划，不自动改价；禁止描述为千牛/客户运营平台能力；"
            "email-sequence 必须带站外声明；缺数据源时 unavailable。"
        ),
    ),
    ExpertProfile(
        key="ecom_ads_strategy_analyzer_executor",
        name="广告策略与分析",
        kind=WorkshopExpertKind.EXECUTOR,
        skill_refs=(
            "ads",
            "ecommerce-ppc-strategy-planner",
        ),
        capability_allowlist=frozenset(
            _READ
            | {
                WorkshopToolCapability.WRITE_PROJECT_FILES,
                WorkshopToolCapability.WRITE_TEMP_WORKSPACE,
                WorkshopToolCapability.SANDBOX_EXECUTE,
            }
        ),
        deliverable_types=("AdsStrategyBrief", "AdsReportDiagnosis"),
        beta=True,
        system_prompt_boundary=(
            "V1 只读：无账户写权限；禁止改预算/出价/投放设置；"
            "万相台金额 UNIT_UNVERIFIED 只展示 raw，禁止当 fen 算 ROAS；"
            "阿里妈妈引用必须带时效风险句；缺单位/数据源时 unavailable。"
        ),
    ),
    ExpertProfile(
        key="ecom_ops_analytics_executor",
        name="经营分析与复盘",
        kind=WorkshopExpertKind.EXECUTOR,
        skill_refs=(
            "performance-report",
            "warehouse-optimization",
            "pricing",
        ),
        capability_allowlist=frozenset(
            _READ
            | {
                WorkshopToolCapability.WRITE_PROJECT_FILES,
                WorkshopToolCapability.WRITE_TEMP_WORKSPACE,
                WorkshopToolCapability.SANDBOX_EXECUTE,
                WorkshopToolCapability.REQUEST_EXTERNAL_AUTH,
            }
        ),
        deliverable_types=(
            "MetricsSnapshot",
            "MetricsWorkbook",
            "ReviewDeck",
            "ChartArtifact",
            "DailyOrWeeklyReview",
            "AnomalyList",
        ),
        system_prompt_boundary=(
            "禁止口算替代码；只计算有真实来源的数据；生意参谋走官方导出导入，"
            "不宣称 TOP 自动拉取；无导入映射时 blocked/unavailable；"
            "不改正式售价/库存（只建议）；缺失值不得显示为 0。"
        ),
    ),
    ExpertProfile(
        key="ecom_taobao_store_ops_executor",
        name="淘天店铺运营",
        kind=WorkshopExpertKind.EXECUTOR,
        skill_refs=(
            "taobao-open-platform-publish",
            "browser",
        ),
        capability_allowlist=frozenset(
            {
                WorkshopToolCapability.READ_UPLOADS,
                WorkshopToolCapability.READ_PROJECT_FILES,
                WorkshopToolCapability.BROWSER_READ,
                WorkshopToolCapability.BROWSER_WRITE,
                WorkshopToolCapability.TAOBAO_STORE_WRITE,
                WorkshopToolCapability.REQUEST_EXTERNAL_AUTH,
                WorkshopToolCapability.PROPOSE_INVITE,
            }
        ),
        deliverable_types=("PublishDiff", "PublishReceipt", "AuditLogRef"),
        system_prompt_boundary=(
            "仅淘天；上下架映射 upshelf/downshelf；发货与上下架必须 Diff+用户确认；"
            "无 external_auth 不调用写；Token 过期/限流/5xx 不自动 Browser；"
            "失败进入 blocked；缺能力 unsupported。"
        ),
    ),
)

ECOM_PRESET_KEYS: tuple[str, ...] = tuple(p.key for p in ECOM_PROFILES)


def get_ecom_profile(key: str) -> ExpertProfile:
    """按 key 读取电商 Profile；不存在则抛 KeyError"""
    for profile in ECOM_PROFILES:
        if profile.key == key:
            return profile
    raise KeyError(key)


def is_ecom_preset(key: str) -> bool:
    """是否为电商包预置 key"""
    return key in ECOM_PRESET_KEYS
