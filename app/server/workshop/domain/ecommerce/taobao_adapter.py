from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal, Protocol, Sequence

from app.contracts.ecommerce import PublishDiff, PublishReceipt
from app.server.workshop.domain.ecommerce.authorized_operations import (
    AuthorizedOperation,
    AuthorizedOperationError,
    authorize_operation,
    stable_payload_hash,
)
from app.server.workshop.domain.enums import WorkshopToolCapability

TAOBAO_SCOPE_MANIFEST_MISSING = "TAOBAO_SCOPE_MANIFEST_MISSING"
TAOBAO_SCOPE_INSUFFICIENT = "TAOBAO_SCOPE_INSUFFICIENT"
TAOBAO_TOKEN_EXPIRED = "TAOBAO_TOKEN_EXPIRED"
TAOBAO_RATE_LIMIT = "TAOBAO_RATE_LIMIT"
TAOBAO_SERVER_ERROR = "TAOBAO_SERVER_ERROR"
BROWSER_FALLBACK_REQUIRES_EVIDENCE = "BROWSER_FALLBACK_REQUIRES_EVIDENCE"


class TaobaoAdapterError(Exception):
    """淘天 Adapter 错误"""

    def __init__(self, code: str, message: str) -> None:
        """初始化"""
        super().__init__(message)
        self.code = code
        self.message = message


class TaobaoOperation(str, Enum):
    PUBLISH = "publish"
    EDIT = "edit"
    LIST = "list"  # UX alias → upshelf
    UNLIST = "unlist"  # UX alias → downshelf
    UPSHELF = "upshelf"
    DOWNSHELF = "downshelf"


# Official platform methods (research §14). Never map list/unlist to onsale.get.
PLATFORM_UPSHELF = "alibaba.item.operate.upshelf"
PLATFORM_DOWNSHELF = "alibaba.item.operate.downshelf"
FORBIDDEN_LIST_MAPPING = "taobao.items.onsale.get"


def map_ux_shelf_operation_to_platform(operation: str) -> str:
    """将产品 UX 的 list/unlist 或 upshelf/downshelf 映射到官方方法"""
    if operation in (TaobaoOperation.LIST.value, TaobaoOperation.UPSHELF.value, "list", "upshelf"):
        return PLATFORM_UPSHELF
    if operation in (TaobaoOperation.UNLIST.value, TaobaoOperation.DOWNSHELF.value, "unlist", "downshelf"):
        return PLATFORM_DOWNSHELF
    raise TaobaoAdapterError("TAOBAO_OPERATION_UNSUPPORTED", f"not a shelf op: {operation}")


@dataclass(frozen=True, slots=True)
class TaobaoScopeManifest:
    product_schema_read: bool = False
    product_write: bool = False
    product_status_write: bool = False


class TaobaoAdapter(Protocol):
    """淘天 Open Platform Adapter 端口（research §14）"""

    def ensure_connection(self, *, shop_connection_id: str) -> dict[str, Any]:
        """确保店铺连接可用"""
        ...

    def get_authorized_cats(self, *, shop_connection_id: str) -> dict[str, Any]:
        """读取已授权类目"""
        ...

    def get_itemcats(self, *, parent_cid: str | None = None) -> dict[str, Any]:
        """读取商品类目"""
        ...

    def product_match(self, *, shop_connection_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """匹配已有产品"""
        ...

    def product_add(self, *, shop_connection_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """新增产品"""
        ...

    def get_publish_schema(self, *, shop_connection_id: str, cat_id: str) -> dict[str, Any]:
        """读取发布 schema"""
        ...

    def get_publish_props(self, *, shop_connection_id: str, cat_id: str) -> dict[str, Any]:
        """读取发布属性"""
        ...

    def upload_picture(self, *, shop_connection_id: str, image_bytes: bytes) -> dict[str, Any]:
        """上传图片"""
        ...

    def create_product(
        self,
        *,
        shop_connection_id: str,
        diff: PublishDiff,
        idempotency_key: str,
    ) -> PublishReceipt:
        """创建产品"""
        ...

    def get_item(self, *, shop_connection_id: str, item_id: str) -> dict[str, Any]:
        """读取商品详情"""
        ...

    def list_onsale(self, *, shop_connection_id: str, page_no: int = 1) -> dict[str, Any]:
        """列出在售商品"""
        ...

    def list_inventory(self, *, shop_connection_id: str, page_no: int = 1) -> dict[str, Any]:
        """列出库存商品"""
        ...

    def get_edit_schema(self, *, shop_connection_id: str, item_id: str) -> dict[str, Any]:
        """读取编辑 schema"""
        ...

    def edit_product(
        self,
        *,
        shop_connection_id: str,
        diff: PublishDiff,
        idempotency_key: str,
    ) -> PublishReceipt:
        """编辑商品"""
        ...

    def fastupdate(self, *, shop_connection_id: str, item_id: str, fields: dict[str, Any]) -> dict[str, Any]:
        """快速更新商品字段"""
        ...

    def upshelf_product(
        self,
        *,
        shop_connection_id: str,
        item_id: str,
        idempotency_key: str,
    ) -> PublishReceipt:
        """上架商品"""
        ...

    def downshelf_product(
        self,
        *,
        shop_connection_id: str,
        item_id: str,
        idempotency_key: str,
    ) -> PublishReceipt:
        """下架商品"""
        ...

    def delete_product(
        self,
        *,
        shop_connection_id: str,
        item_id: str,
        idempotency_key: str,
    ) -> PublishReceipt:
        """删除商品"""
        ...

    def update_sku(self, *, shop_connection_id: str, item_id: str, sku: dict[str, Any]) -> dict[str, Any]:
        """更新 SKU"""
        ...

    def sync_trades_increment(self, *, shop_connection_id: str, start: str, end: str) -> dict[str, Any]:
        """增量同步交易"""
        ...

    def sync_trades_incrementv(self, *, shop_connection_id: str, start: str, end: str) -> dict[str, Any]:
        """增量同步交易（v 接口）"""
        ...

    def get_trade(self, *, shop_connection_id: str, tid: str) -> dict[str, Any]:
        """读取交易详情"""
        ...

    def sync_refunds(self, *, shop_connection_id: str, start: str, end: str) -> dict[str, Any]:
        """同步退款"""
        ...

    def get_refund(self, *, shop_connection_id: str, refund_id: str) -> dict[str, Any]:
        """读取退款详情"""
        ...

    def ship_online(
        self,
        *,
        shop_connection_id: str,
        tid: str,
        company_code: str,
        out_sid: str | None,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """在线下单发货"""
        ...

    def get_rates(self, *, shop_connection_id: str, tid: str) -> dict[str, Any]:
        """读取评价"""
        ...

    def get_shop(self, *, shop_connection_id: str) -> dict[str, Any]:
        """读取店铺信息"""
        ...

    def ads_report_universalbp(self, *, shop_connection_id: str, query: dict[str, Any]) -> dict[str, Any]:
        """拉取 UniversalBP 广告报表"""
        ...

    def import_official_export(self, *, shop_connection_id: str, export_kind: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
        """导入官方导出数据"""
        ...

    # UX aliases — must call upshelf/downshelf, never onsale.get
    def list_product(
        self,
        *,
        shop_connection_id: str,
        item_id: str,
        idempotency_key: str,
    ) -> PublishReceipt:
        """上架商品（UX 别名）"""
        ...

    def unlist_product(
        self,
        *,
        shop_connection_id: str,
        item_id: str,
        idempotency_key: str,
    ) -> PublishReceipt:
        """下架商品（UX 别名）"""
        ...


def compute_publish_diff_payload_hash(diff: PublishDiff) -> str:
    """PublishDiff 不可变 payload_hash"""
    payload = {
        "operation": diff.operation,
        "shop_connection_id": diff.shop_connection_id,
        "item_id": diff.item_id,
        "sku_ids": sorted(diff.sku_ids),
        "base_version": diff.base_version,
        "target_version": diff.target_version,
        "changes": [
            {"path": c.path, "before": c.before, "after": c.after} for c in diff.changes
        ],
        "idempotency_key": diff.idempotency_key,
    }
    return stable_payload_hash(payload)


def require_scope_manifest(manifest: TaobaoScopeManifest | None) -> TaobaoScopeManifest:
    """scope manifest 为空则 fail closed"""
    if manifest is None:
        raise TaobaoAdapterError(
            TAOBAO_SCOPE_MANIFEST_MISSING,
            "taobao scope manifest missing",
        )
    if not (
        manifest.product_schema_read
        and manifest.product_write
        and manifest.product_status_write
    ):
        raise TaobaoAdapterError(
            TAOBAO_SCOPE_INSUFFICIENT,
            "taobao scope manifest insufficient",
        )
    return manifest


def assert_taobao_store_write_allowed(
    *,
    granted_external: Sequence[WorkshopToolCapability],
    authorized_ops: Sequence[AuthorizedOperation],
    diff: PublishDiff,
    task_id: str,
) -> AuthorizedOperation:
    """API 写路径需 TAOBAO_STORE_WRITE grant + 匹配 Diff hash"""
    payload_hash = diff.payload_hash or compute_publish_diff_payload_hash(diff)
    try:
        return authorize_operation(
            granted_external=granted_external,
            authorized_ops=authorized_ops,
            capability=WorkshopToolCapability.TAOBAO_STORE_WRITE,
            payload_hash=payload_hash,
            task_id=task_id,
        )
    except AuthorizedOperationError as exc:
        raise TaobaoAdapterError("TAOBAO_AUTH_DENIED", str(exc)) from exc


def assert_browser_fallback_allowed(
    *,
    granted_external: Sequence[WorkshopToolCapability],
    authorized_ops: Sequence[AuthorizedOperation],
    fallback_reason: str | None,
    evidence_ref: str | None,
    operation_payload: dict[str, Any],
    task_id: str,
) -> AuthorizedOperation:
    """Browser fallback 与 TAOBAO_STORE_WRITE 独立；需 reason + evidence + BROWSER_WRITE grant"""
    if not fallback_reason or not evidence_ref:
        raise TaobaoAdapterError(
            BROWSER_FALLBACK_REQUIRES_EVIDENCE,
            "browser fallback requires fallback_reason and evidence_ref",
        )
    payload_hash = stable_payload_hash(operation_payload)
    try:
        return authorize_operation(
            granted_external=granted_external,
            authorized_ops=authorized_ops,
            capability=WorkshopToolCapability.BROWSER_WRITE,
            payload_hash=payload_hash,
            task_id=task_id,
        )
    except AuthorizedOperationError as exc:
        raise TaobaoAdapterError("BROWSER_AUTH_DENIED", str(exc)) from exc


@dataclass
class FakeTaobaoAdapter:
    """凭据无关 fake Adapter；支持幂等与错误注入"""

    manifest: TaobaoScopeManifest | None = field(
        default_factory=lambda: TaobaoScopeManifest(
            product_schema_read=True,
            product_write=True,
            product_status_write=True,
        )
    )
    token_expired: bool = False
    rate_limited: bool = False
    server_error: bool = False
    _published: dict[str, PublishReceipt] = field(default_factory=dict)
    last_platform_methods: list[str] = field(default_factory=list)
    _calls: list[str] = field(default_factory=list)

    def _guard(self) -> None:
        """写操作前校验 scope 与授权"""
        require_scope_manifest(self.manifest)
        if self.token_expired:
            raise TaobaoAdapterError(TAOBAO_TOKEN_EXPIRED, "access token expired")
        if self.rate_limited:
            raise TaobaoAdapterError(TAOBAO_RATE_LIMIT, "rate limit exceeded")
        if self.server_error:
            raise TaobaoAdapterError(TAOBAO_SERVER_ERROR, "upstream 5xx")

    def _receipt(
        self,
        *,
        diff: PublishDiff,
        idempotency_key: str,
        platform_item_id: str,
    ) -> PublishReceipt:
        """构造发布回执"""
        now = datetime.now(timezone.utc)
        payload_hash = diff.payload_hash or compute_publish_diff_payload_hash(diff)
        return PublishReceipt(
            schema_version=diff.schema_version,
            project_id=diff.project_id,
            task_id=diff.task_id,
            expert_id=diff.expert_id,
            created_at=now,
            source_refs=list(diff.source_refs),
            operation=diff.operation,
            shop_connection_id=diff.shop_connection_id,
            platform_item_id=platform_item_id,
            sku_ids=list(diff.sku_ids),
            request_id=f"req_{idempotency_key[:12]}",
            idempotency_key=idempotency_key,
            payload_hash=payload_hash,
            status="success",
            executed_at=now,
            platform_response_code="200",
            audit_log_ref=f"audit_{idempotency_key[:12]}",
        )

    def create_product(
        self,
        *,
        shop_connection_id: str,
        diff: PublishDiff,
        idempotency_key: str,
    ) -> PublishReceipt:
        """创建产品"""
        self._guard()
        if idempotency_key in self._published:
            return self._published[idempotency_key]
        platform_item_id = f"item_{hashlib.sha256(idempotency_key.encode()).hexdigest()[:12]}"
        receipt = self._receipt(
            diff=diff,
            idempotency_key=idempotency_key,
            platform_item_id=platform_item_id,
        )
        self._published[idempotency_key] = receipt
        return receipt

    def edit_product(
        self,
        *,
        shop_connection_id: str,
        diff: PublishDiff,
        idempotency_key: str,
    ) -> PublishReceipt:
        """编辑商品"""
        self._guard()
        if idempotency_key in self._published:
            return self._published[idempotency_key]
        item_id = diff.item_id or f"item_unknown"
        receipt = self._receipt(
            diff=diff,
            idempotency_key=idempotency_key,
            platform_item_id=item_id,
        )
        self._published[idempotency_key] = receipt
        return receipt

    def _shelf_receipt(
        self,
        *,
        shop_connection_id: str,
        item_id: str,
        idempotency_key: str,
        ux_operation: Literal["list", "unlist"],
        platform_method: str,
    ) -> PublishReceipt:
        """构造上下架回执"""
        if platform_method == FORBIDDEN_LIST_MAPPING:
            raise TaobaoAdapterError(
                "TAOBAO_FORBIDDEN_MAPPING",
                "list/unlist must not map to taobao.items.onsale.get",
            )
        self.last_platform_methods.append(platform_method)
        self._calls.append(platform_method)
        if idempotency_key in self._published:
            return self._published[idempotency_key]
        now = datetime.now(timezone.utc)
        receipt = PublishReceipt(
            schema_version="ecom-v1",
            project_id="",
            task_id="",
            expert_id="",
            created_at=now,
            source_refs=[],
            operation=ux_operation,
            shop_connection_id=shop_connection_id,
            platform_item_id=item_id,
            sku_ids=[],
            request_id=f"req_{idempotency_key[:12]}",
            idempotency_key=idempotency_key,
            payload_hash=hashlib.sha256(
                json.dumps(
                    {"item_id": item_id, "platform_method": platform_method},
                    sort_keys=True,
                ).encode()
            ).hexdigest(),
            status="success",
            executed_at=now,
            platform_response_code="200",
            audit_log_ref=f"audit_{idempotency_key[:12]}",
        )
        self._published[idempotency_key] = receipt
        return receipt

    def upshelf_product(
        self,
        *,
        shop_connection_id: str,
        item_id: str,
        idempotency_key: str,
    ) -> PublishReceipt:
        """上架商品"""
        self._guard()
        return self._shelf_receipt(
            shop_connection_id=shop_connection_id,
            item_id=item_id,
            idempotency_key=idempotency_key,
            ux_operation=TaobaoOperation.LIST.value,
            platform_method=PLATFORM_UPSHELF,
        )

    def downshelf_product(
        self,
        *,
        shop_connection_id: str,
        item_id: str,
        idempotency_key: str,
    ) -> PublishReceipt:
        """下架商品"""
        self._guard()
        return self._shelf_receipt(
            shop_connection_id=shop_connection_id,
            item_id=item_id,
            idempotency_key=idempotency_key,
            ux_operation=TaobaoOperation.UNLIST.value,
            platform_method=PLATFORM_DOWNSHELF,
        )

    def list_product(
        self,
        *,
        shop_connection_id: str,
        item_id: str,
        idempotency_key: str,
    ) -> PublishReceipt:
        """UX 上架别名；映射到 upshelf，禁止 onsale.get"""
        return self.upshelf_product(
            shop_connection_id=shop_connection_id,
            item_id=item_id,
            idempotency_key=idempotency_key,
        )

    def unlist_product(
        self,
        *,
        shop_connection_id: str,
        item_id: str,
        idempotency_key: str,
    ) -> PublishReceipt:
        """UX 下架别名；映射到 downshelf，禁止 onsale.get"""
        return self.downshelf_product(
            shop_connection_id=shop_connection_id,
            item_id=item_id,
            idempotency_key=idempotency_key,
        )

    def ensure_connection(self, *, shop_connection_id: str) -> dict[str, Any]:
        """确保店铺连接可用"""
        self._guard()
        self._calls.append("ensure_connection")
        return {"shop_connection_id": shop_connection_id, "status": "connected"}

    def get_authorized_cats(self, *, shop_connection_id: str) -> dict[str, Any]:
        """读取已授权类目"""
        self._guard()
        self._calls.append("taobao.itemcats.authorize.get")
        return {"shop_connection_id": shop_connection_id, "cats": []}

    def get_itemcats(self, *, parent_cid: str | None = None) -> dict[str, Any]:
        """读取商品类目"""
        self._guard()
        self._calls.append("taobao.itemcats.get")
        return {"parent_cid": parent_cid, "itemcats": []}

    def product_match(self, *, shop_connection_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """匹配已有产品"""
        self._guard()
        self._calls.append("product_match")
        return {"matched": False, "payload_keys": sorted(payload)}

    def product_add(self, *, shop_connection_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """新增产品"""
        self._guard()
        self._calls.append("product_add")
        return {"ok": True}

    def get_publish_schema(self, *, shop_connection_id: str, cat_id: str) -> dict[str, Any]:
        """读取发布 schema"""
        self._guard()
        self._calls.append("get_publish_schema")
        # Title limits come from runtime schema — not hardcoded constants.
        return {
            "cat_id": cat_id,
            "fields": {
                "title": {"type": "string", "max_length_from_schema": True},
            },
        }

    def get_publish_props(self, *, shop_connection_id: str, cat_id: str) -> dict[str, Any]:
        """读取发布属性"""
        self._guard()
        self._calls.append("get_publish_props")
        return {"cat_id": cat_id, "props": []}

    def upload_picture(self, *, shop_connection_id: str, image_bytes: bytes) -> dict[str, Any]:
        """上传图片"""
        self._guard()
        self._calls.append("taobao.picture.upload")
        return {"picture_id": f"pic_{len(image_bytes)}"}

    def get_item(self, *, shop_connection_id: str, item_id: str) -> dict[str, Any]:
        """读取商品详情"""
        self._guard()
        self._calls.append("get_item")
        return {"item_id": item_id}

    def list_onsale(self, *, shop_connection_id: str, page_no: int = 1) -> dict[str, Any]:
        """列出在售商品"""
        self._guard()
        self._calls.append("taobao.items.onsale.get")
        return {"page_no": page_no, "items": []}

    def list_inventory(self, *, shop_connection_id: str, page_no: int = 1) -> dict[str, Any]:
        """列出库存商品"""
        self._guard()
        self._calls.append("taobao.items.inventory.get")
        return {"page_no": page_no, "items": []}

    def get_edit_schema(self, *, shop_connection_id: str, item_id: str) -> dict[str, Any]:
        """读取编辑 schema"""
        self._guard()
        self._calls.append("get_edit_schema")
        return {"item_id": item_id, "fields": {}}

    def fastupdate(self, *, shop_connection_id: str, item_id: str, fields: dict[str, Any]) -> dict[str, Any]:
        """快速更新商品字段"""
        self._guard()
        self._calls.append("fastupdate")
        return {"item_id": item_id, "updated": sorted(fields)}

    def delete_product(
        self,
        *,
        shop_connection_id: str,
        item_id: str,
        idempotency_key: str,
    ) -> PublishReceipt:
        """删除商品"""
        self._guard()
        self._calls.append("delete_product")
        now = datetime.now(timezone.utc)
        return PublishReceipt(
            schema_version="ecom-v1",
            project_id="",
            task_id="",
            expert_id="",
            created_at=now,
            source_refs=[],
            operation="edit",
            shop_connection_id=shop_connection_id,
            platform_item_id=item_id,
            sku_ids=[],
            request_id=f"req_{idempotency_key[:12]}",
            idempotency_key=idempotency_key,
            payload_hash=hashlib.sha256(idempotency_key.encode()).hexdigest(),
            status="success",
            executed_at=now,
            platform_response_code="200",
            audit_log_ref=f"audit_{idempotency_key[:12]}",
        )

    def update_sku(self, *, shop_connection_id: str, item_id: str, sku: dict[str, Any]) -> dict[str, Any]:
        """更新 SKU"""
        self._guard()
        self._calls.append("update_sku")
        return {"item_id": item_id, "sku": sku}

    def sync_trades_increment(self, *, shop_connection_id: str, start: str, end: str) -> dict[str, Any]:
        """增量同步交易"""
        self._guard()
        self._calls.append("taobao.trades.sold.increment.get")
        return {"trades": [], "start": start, "end": end}

    def sync_trades_incrementv(self, *, shop_connection_id: str, start: str, end: str) -> dict[str, Any]:
        """增量同步交易（v 接口）"""
        self._guard()
        self._calls.append("taobao.trades.sold.incrementv.get")
        return {"trades": [], "start": start, "end": end}

    def get_trade(self, *, shop_connection_id: str, tid: str) -> dict[str, Any]:
        """读取交易详情"""
        self._guard()
        self._calls.append("taobao.trade.fullinfo.get")
        return {"tid": tid}

    def sync_refunds(self, *, shop_connection_id: str, start: str, end: str) -> dict[str, Any]:
        """同步退款"""
        self._guard()
        self._calls.append("sync_refunds")
        return {"refunds": [], "start": start, "end": end}

    def get_refund(self, *, shop_connection_id: str, refund_id: str) -> dict[str, Any]:
        """读取退款详情"""
        self._guard()
        self._calls.append("taobao.refund.get")
        return {"refund_id": refund_id}

    def ship_online(
        self,
        *,
        shop_connection_id: str,
        tid: str,
        company_code: str,
        out_sid: str | None,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """在线下单发货"""
        self._guard()
        self._calls.append("taobao.logistics.online.send")
        return {
            "tid": tid,
            "company_code": company_code,
            "out_sid": out_sid,
            "idempotency_key": idempotency_key,
        }

    def get_rates(self, *, shop_connection_id: str, tid: str) -> dict[str, Any]:
        """读取评价"""
        self._guard()
        self._calls.append("taobao.traderates.get")
        return {"tid": tid, "rates": []}

    def get_shop(self, *, shop_connection_id: str) -> dict[str, Any]:
        """读取店铺信息"""
        self._guard()
        self._calls.append("taobao.shop.seller.get")
        return {"shop_connection_id": shop_connection_id}

    def ads_report_universalbp(self, *, shop_connection_id: str, query: dict[str, Any]) -> dict[str, Any]:
        """只读；金额保持 raw，UNIT_UNVERIFIED"""
        self._guard()
        self._calls.append("ads_report_universalbp")
        from app.server.workshop.domain.ecommerce.money import extract_unit_unverified_from_row

        sample = {
            "charge": "xxxxx",
            "alipay_inshop_amt": "xxxxx",
            "roi": "2.5",
            "ctr": "0.01",
        }
        raw_amounts = extract_unit_unverified_from_row(sample)
        return {
            "shop_connection_id": shop_connection_id,
            "query": query,
            "rows": [
                {
                    "spend_raw": raw_amounts["charge"].raw_string,
                    "revenue_raw": raw_amounts["alipay_inshop_amt"].raw_string,
                    "unit_status": "UNIT_UNVERIFIED",
                    "official_roi": sample["roi"],
                    "official_ctr": sample["ctr"],
                }
            ],
        }

    def import_official_export(
        self, *, shop_connection_id: str, export_kind: str, rows: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """导入官方导出数据"""
        self._guard()
        self._calls.append("import_official_export")
        return {
            "shop_connection_id": shop_connection_id,
            "export_kind": export_kind,
            "row_count": len(rows),
            "status": "accepted_for_mapping",
        }
