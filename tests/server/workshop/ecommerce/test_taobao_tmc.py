from __future__ import annotations

from app.server.workshop.domain.ecommerce.refund import RefundStore, aggregate_success_refund_fee_fen
from app.server.workshop.domain.ecommerce.tmc import (
    RecordingRestPort,
    TmcAccelerator,
    TmcMessage,
)


def _trade_msg(msg_id: str, tid: str, topic: str = "taobao_trade_TradeBuyerPay") -> TmcMessage:
    """构造交易 TMC 消息"""
    return TmcMessage(msg_id=msg_id, topic=topic, content={"tid": tid, "oid": "1"})


def test_duplicate_msg_id_idempotent() -> None:
    """覆盖 duplicate msg id idempotent"""
    rest = RecordingRestPort()
    accel = TmcAccelerator(rest=rest, refund_store=RefundStore())
    msg = _trade_msg("m1", "tid_1")
    assert accel.handle(msg).confirmed is True
    assert accel.handle(msg).confirmed is True
    assert rest.trade_calls == ["tid_1"]


def test_different_msg_id_same_business_event_not_duplicated() -> None:
    """覆盖 different msg id same business event not duplicated"""
    rest = RecordingRestPort()
    accel = TmcAccelerator(rest=rest, refund_store=RefundStore())
    assert accel.handle(_trade_msg("m1", "tid_1")).confirmed is True
    assert accel.handle(_trade_msg("m2", "tid_1")).confirmed is True
    assert rest.trade_calls == ["tid_1"]


def test_out_of_order_messages_ok() -> None:
    """覆盖 out of order messages ok"""
    rest = RecordingRestPort()
    accel = TmcAccelerator(rest=rest, refund_store=RefundStore())
    # success before buyer pay — still triggers independent REST refresh
    assert accel.handle(
        _trade_msg("m_success", "tid_9", topic="taobao_trade_TradeSuccess")
    ).confirmed
    assert accel.handle(
        _trade_msg("m_pay", "tid_9", topic="taobao_trade_TradeBuyerPay")
    ).confirmed
    assert rest.trade_calls == ["tid_9", "tid_9"]


def test_failure_does_not_confirm() -> None:
    """覆盖 failure does not confirm"""
    rest = RecordingRestPort(fail_trade={"tid_x"})
    accel = TmcAccelerator(rest=rest, refund_store=RefundStore())
    result = accel.handle(_trade_msg("m_fail", "tid_x"))
    assert result.confirmed is False
    assert "m_fail" not in accel.confirmed_msg_ids


def test_success_confirms() -> None:
    """覆盖 success confirms"""
    rest = RecordingRestPort()
    accel = TmcAccelerator(rest=rest, refund_store=RefundStore())
    result = accel.handle(_trade_msg("m_ok", "tid_ok"))
    assert result.confirmed is True
    assert "m_ok" in accel.confirmed_msg_ids


def test_rest_compensation_restores_after_tmc_gap() -> None:
    """覆盖 rest compensation restores after tmc gap"""
    rest = RecordingRestPort(
        trades={
            "tid_miss": {
                "tid": "tid_miss",
                "status": "TRADE_FINISHED",
                "payment": "10.00",
                "pay_time": "2026-07-01 10:00:00",
            }
        }
    )
    accel = TmcAccelerator(rest=rest, refund_store=RefundStore())
    # TMC never delivered; REST compensation recovers.
    accel.rest_compensate_trades(["tid_miss"])
    assert accel.trade_snapshots["tid_miss"]["status"] == "TRADE_FINISHED"


def test_refund_message_goes_through_refund_version_snapshot() -> None:
    """覆盖 refund message goes through refund version snapshot"""
    rest = RecordingRestPort(
        refunds={
            "r1": {
                "refund_id": "r1",
                "refund_version": 2,
                "status": "SUCCESS",
                "refund_fee": "10.00",
                "oid": "o1",
                "end_time": "2026-07-02 12:00:00",
                "modified": "2026-07-02 12:00:00",
            }
        }
    )
    store = RefundStore()
    accel = TmcAccelerator(rest=rest, refund_store=store)
    # Stale direct event first
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
    msg = TmcMessage(
        msg_id="rm1",
        topic="taobao_refund_RefundSuccess",
        content={"refund_id": "r1", "tid": "t1"},
    )
    assert accel.handle(msg).confirmed is True
    # Older version via TMC content alone must not bypass — REST refresh supplies version 2.
    assert store.current_snapshot("r1").refund_version == 2
    assert aggregate_success_refund_fee_fen(store) == 1000
    # Out-of-order older REST payload cannot roll back
    accel.rest_compensate_refunds(
        [
            {
                "refund_id": "r1",
                "refund_version": 1,
                "status": "CLOSED",
                "refund_fee": "10.00",
                "oid": "o1",
                "end_time": None,
                "modified": "2026-07-01 10:00:00",
            }
        ]
    )
    assert store.current_snapshot("r1").status == "SUCCESS"
