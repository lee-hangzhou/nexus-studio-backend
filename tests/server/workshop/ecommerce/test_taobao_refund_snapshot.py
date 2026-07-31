from __future__ import annotations

from datetime import date

from app.server.workshop.domain.ecommerce.refund import (
    RefundStore,
    aggregate_success_refund_fee_fen,
    net_paid_after_refunds,
)


def test_same_refund_multiple_versions_count_once() -> None:
    """覆盖 same refund multiple versions count once"""
    store = RefundStore()
    store.apply_event(
        {
            "refund_id": "r1",
            "refund_version": 1,
            "status": "WAIT_SELLER_AGREE",
            "refund_fee": "10.00",
            "oid": "o1",
            "end_time": None,
            "modified": "2026-07-01 10:00:00",
        }
    )
    store.apply_event(
        {
            "refund_id": "r1",
            "refund_version": 2,
            "status": "SUCCESS",
            "refund_fee": "10.00",
            "oid": "o1",
            "end_time": "2026-07-02 12:00:00",
            "modified": "2026-07-02 12:00:00",
        }
    )
    assert aggregate_success_refund_fee_fen(store) == 1000


def test_out_of_order_old_version_does_not_overwrite() -> None:
    """覆盖 out of order old version does not overwrite"""
    store = RefundStore()
    store.apply_event(
        {
            "refund_id": "r2",
            "refund_version": 3,
            "status": "SUCCESS",
            "refund_fee": "20.00",
            "oid": "o1",
            "end_time": "2026-07-03 10:00:00",
            "modified": "2026-07-03 10:00:00",
        }
    )
    store.apply_event(
        {
            "refund_id": "r2",
            "refund_version": 1,
            "status": "CLOSED",
            "refund_fee": "20.00",
            "oid": "o1",
            "end_time": None,
            "modified": "2026-07-01 09:00:00",
        }
    )
    snap = store.current_snapshot("r2")
    assert snap is not None
    assert snap.status == "SUCCESS"
    assert snap.refund_version == 3
    assert aggregate_success_refund_fee_fen(store) == 2000


def test_duplicate_event_idempotent() -> None:
    """覆盖 duplicate event idempotent"""
    store = RefundStore()
    event = {
        "refund_id": "r3",
        "refund_version": 1,
        "status": "SUCCESS",
        "refund_fee": "5.00",
        "oid": "o1",
        "end_time": "2026-07-04 10:00:00",
        "modified": "2026-07-04 10:00:00",
    }
    store.apply_event(event)
    store.apply_event(event)
    assert aggregate_success_refund_fee_fen(store) == 500


def test_multiple_independent_partial_refunds_sum() -> None:
    """覆盖 multiple independent partial refunds sum"""
    store = RefundStore()
    store.apply_event(
        {
            "refund_id": "r4a",
            "refund_version": 1,
            "status": "SUCCESS",
            "refund_fee": "30.00",
            "oid": "o9",
            "end_time": "2026-07-05 10:00:00",
            "modified": "2026-07-05 10:00:00",
        }
    )
    store.apply_event(
        {
            "refund_id": "r4b",
            "refund_version": 1,
            "status": "SUCCESS",
            "refund_fee": "20.00",
            "oid": "o9",
            "end_time": "2026-07-06 10:00:00",
            "modified": "2026-07-06 10:00:00",
        }
    )
    assert aggregate_success_refund_fee_fen(store) == 5000


def test_closed_or_rejected_not_in_refund_fee() -> None:
    """覆盖 closed or rejected not in refund fee"""
    store = RefundStore()
    store.apply_event(
        {
            "refund_id": "r5",
            "refund_version": 1,
            "status": "CLOSED",
            "refund_fee": "99.00",
            "oid": "o1",
            "end_time": "2026-07-07 10:00:00",
            "modified": "2026-07-07 10:00:00",
        }
    )
    store.apply_event(
        {
            "refund_id": "r6",
            "refund_version": 1,
            "status": "SELLER_REFUSE_BUYER",
            "refund_fee": "50.00",
            "oid": "o1",
            "end_time": None,
            "modified": "2026-07-07 11:00:00",
        }
    )
    assert aggregate_success_refund_fee_fen(store) == 0


def test_success_then_stale_status_does_not_rollback() -> None:
    """覆盖 success then stale status does not rollback"""
    store = RefundStore()
    store.apply_event(
        {
            "refund_id": "r7",
            "refund_version": 5,
            "status": "SUCCESS",
            "refund_fee": "15.00",
            "oid": "o1",
            "end_time": "2026-07-08 10:00:00",
            "modified": "2026-07-08 10:00:00",
        }
    )
    store.apply_event(
        {
            "refund_id": "r7",
            "refund_version": 2,
            "status": "WAIT_SELLER_AGREE",
            "refund_fee": "15.00",
            "oid": "o1",
            "end_time": None,
            "modified": "2026-07-07 10:00:00",
        }
    )
    assert store.current_snapshot("r7").status == "SUCCESS"
    assert aggregate_success_refund_fee_fen(store) == 1500


def test_cross_period_refund_affects_net_on_refund_day_not_pay_day() -> None:
    """覆盖 cross period refund affects net on refund day not pay day"""
    store = RefundStore()
    store.apply_event(
        {
            "refund_id": "r8",
            "refund_version": 1,
            "status": "SUCCESS",
            "refund_fee": "40.00",
            "oid": "o1",
            "end_time": "2026-08-01 10:00:00",
            "modified": "2026-08-01 10:00:00",
        }
    )
    pay_day_gmv = 10000
    net = net_paid_after_refunds(
        paid_gmv_fen=pay_day_gmv,
        refund_store=store,
        refund_window_start=date(2026, 8, 1),
        refund_window_end=date(2026, 8, 1),
    )
    assert net == 6000
    # Pay-day window alone does not include this refund.
    net_pay_window = net_paid_after_refunds(
        paid_gmv_fen=pay_day_gmv,
        refund_store=store,
        refund_window_start=date(2026, 7, 1),
        refund_window_end=date(2026, 7, 31),
    )
    assert net_pay_window == 10000
