from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.contracts.ecommerce import PublishChange, PublishDiff
from app.server.workshop.domain.ecommerce.authorized_operations import AuthorizedOperation
from app.server.workshop.domain.ecommerce.taobao_adapter import (
    BROWSER_FALLBACK_REQUIRES_EVIDENCE,
    TAOBAO_RATE_LIMIT,
    TAOBAO_SCOPE_MANIFEST_MISSING,
    TAOBAO_SERVER_ERROR,
    TAOBAO_TOKEN_EXPIRED,
    FakeTaobaoAdapter,
    TaobaoAdapterError,
    TaobaoScopeManifest,
    assert_browser_fallback_allowed,
    assert_taobao_store_write_allowed,
    compute_publish_diff_payload_hash,
    require_scope_manifest,
)
from app.server.workshop.domain.enums import WorkshopToolCapability


def _sample_diff() -> PublishDiff:
    """构造示例 PublishDiff"""
    now = datetime.now(timezone.utc)
    diff = PublishDiff(
        project_id="wp_1",
        task_id="task_1",
        expert_id="expert_store",
        created_at=now,
        operation="publish",
        shop_connection_id="shop_conn_1",
        sku_ids=["sku_1"],
        target_version="v3",
        changes=[PublishChange(path="title", before="旧", after="新")],
        idempotency_key="idem_1",
        payload_hash="",
        expires_at=now,
    )
    return diff.model_copy(update={"payload_hash": compute_publish_diff_payload_hash(diff)})


def test_require_scope_manifest_missing() -> None:
    """空 manifest fail closed"""
    with pytest.raises(TaobaoAdapterError) as exc:
        require_scope_manifest(None)
    assert exc.value.code == TAOBAO_SCOPE_MANIFEST_MISSING


def test_fake_adapter_create_is_idempotent() -> None:
    """重复 idempotency key 不双发"""
    adapter = FakeTaobaoAdapter()
    diff = _sample_diff()
    first = adapter.create_product(
        shop_connection_id="shop_conn_1",
        diff=diff,
        idempotency_key="idem_1",
    )
    second = adapter.create_product(
        shop_connection_id="shop_conn_1",
        diff=diff,
        idempotency_key="idem_1",
    )
    assert first.platform_item_id == second.platform_item_id
    assert first.request_id == second.request_id


def test_fake_adapter_error_classes() -> None:
    """scope/token/rate/5xx 错误可区分"""
    diff = _sample_diff()
    with pytest.raises(TaobaoAdapterError) as exc:
        FakeTaobaoAdapter(manifest=None).create_product(
            shop_connection_id="s", diff=diff, idempotency_key="k1"
        )
    assert exc.value.code == TAOBAO_SCOPE_MANIFEST_MISSING

    with pytest.raises(TaobaoAdapterError) as exc:
        FakeTaobaoAdapter(token_expired=True).create_product(
            shop_connection_id="s", diff=diff, idempotency_key="k2"
        )
    assert exc.value.code == TAOBAO_TOKEN_EXPIRED

    with pytest.raises(TaobaoAdapterError) as exc:
        FakeTaobaoAdapter(rate_limited=True).edit_product(
            shop_connection_id="s", diff=diff, idempotency_key="k3"
        )
    assert exc.value.code == TAOBAO_RATE_LIMIT

    with pytest.raises(TaobaoAdapterError) as exc:
        FakeTaobaoAdapter(server_error=True).unlist_product(
            shop_connection_id="s", item_id="item_1", idempotency_key="k4"
        )
    assert exc.value.code == TAOBAO_SERVER_ERROR


def test_taobao_store_write_requires_matching_authorized_operation() -> None:
    """API 写需 TAOBAO_STORE_WRITE grant + Diff hash 匹配"""
    diff = _sample_diff()
    op = AuthorizedOperation(
        id="op_1",
        task_id="task_1",
        capability=WorkshopToolCapability.TAOBAO_STORE_WRITE,
        operation_kind="publish",
        payload_hash=diff.payload_hash,
        granted_by=1,
        granted_at=datetime.now(timezone.utc),
    )
    matched = assert_taobao_store_write_allowed(
        granted_external=[WorkshopToolCapability.TAOBAO_STORE_WRITE],
        authorized_ops=[op],
        diff=diff,
        task_id="task_1",
    )
    assert matched.id == "op_1"


def test_browser_fallback_separate_from_taobao_write() -> None:
    """Browser fallback 需独立 BROWSER_WRITE 且需 evidence"""
    with pytest.raises(TaobaoAdapterError) as exc:
        assert_browser_fallback_allowed(
            granted_external=[WorkshopToolCapability.TAOBAO_STORE_WRITE],
            authorized_ops=[],
            fallback_reason=None,
            evidence_ref=None,
            operation_payload={"op": "click"},
            task_id="task_1",
        )
    assert exc.value.code == BROWSER_FALLBACK_REQUIRES_EVIDENCE

    op = AuthorizedOperation(
        id="browser_op",
        task_id="task_1",
        capability=WorkshopToolCapability.BROWSER_WRITE,
        operation_kind="browser_write",
        payload_hash="abc",
        granted_by=1,
        granted_at=datetime.now(timezone.utc),
    )
    with pytest.raises(TaobaoAdapterError):
        assert_browser_fallback_allowed(
            granted_external=[WorkshopToolCapability.BROWSER_WRITE],
            authorized_ops=[op],
            fallback_reason="api_gap",
            evidence_ref="doc://gap-001",
            operation_payload={"op": "click"},
            task_id="task_1",
        )


def test_insufficient_scope_manifest() -> None:
    """manifest 缺语义 scope 时拒绝"""
    with pytest.raises(TaobaoAdapterError) as exc:
        require_scope_manifest(
            TaobaoScopeManifest(
                product_schema_read=True,
                product_write=False,
                product_status_write=True,
            )
        )
    assert exc.value.code == "TAOBAO_SCOPE_INSUFFICIENT"


def test_list_unlist_map_to_upshelf_downshelf_not_onsale_get() -> None:
    """UX list/unlist 必须调用官方 upshelf/downshelf，禁止 onsale.get"""
    from app.server.workshop.domain.ecommerce.taobao_adapter import (
        FORBIDDEN_LIST_MAPPING,
        PLATFORM_DOWNSHELF,
        PLATFORM_UPSHELF,
        map_ux_shelf_operation_to_platform,
    )

    assert map_ux_shelf_operation_to_platform("list") == PLATFORM_UPSHELF
    assert map_ux_shelf_operation_to_platform("unlist") == PLATFORM_DOWNSHELF
    assert FORBIDDEN_LIST_MAPPING == "taobao.items.onsale.get"

    adapter = FakeTaobaoAdapter()
    adapter.list_product(shop_connection_id="s", item_id="i1", idempotency_key="up1")
    adapter.unlist_product(shop_connection_id="s", item_id="i1", idempotency_key="down1")
    assert PLATFORM_UPSHELF in adapter.last_platform_methods
    assert PLATFORM_DOWNSHELF in adapter.last_platform_methods
    assert FORBIDDEN_LIST_MAPPING not in adapter.last_platform_methods


def test_fake_adapter_read_ports_and_ads_raw() -> None:
    """§14 端口可响应；UniversalBP 金额保持 UNIT_UNVERIFIED 原始值"""
    adapter = FakeTaobaoAdapter()
    schema = adapter.get_publish_schema(shop_connection_id="s", cat_id="5001")
    assert schema["fields"]["title"]["max_length_from_schema"] is True
    ads = adapter.ads_report_universalbp(shop_connection_id="s", query={"biz_code": "onebp"})
    row = ads["rows"][0]
    assert row["unit_status"] == "UNIT_UNVERIFIED"
    assert "spend_fen" not in row
    assert row["spend_raw"] == "xxxxx"
    assert adapter.import_official_export(
        shop_connection_id="s", export_kind="sycm", rows=[{"a": 1}]
    )["row_count"] == 1
