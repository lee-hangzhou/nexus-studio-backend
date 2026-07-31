from __future__ import annotations

from datetime import date

import pytest

from app.server.workshop.domain.ecommerce.gmv import (
    GmvUnavailableError,
    PaidGmvContribution,
    aggregate_paid_gmv_fen,
    evaluate_paid_gmv,
)


def _trade(
    *,
    tid: str,
    status: str,
    payment: str | None,
    pay_time: str | None,
    orders: list[dict] | None = None,
    received_payment: str | None = None,
) -> dict:
    """构造示例交易"""
    return {
        "tid": tid,
        "status": status,
        "payment": payment,
        "pay_time": pay_time,
        "received_payment": received_payment,
        "orders": orders or [],
    }


def test_unpaid_wait_buyer_pay_not_in_gmv() -> None:
    """覆盖 unpaid wait buyer pay not in gmv"""
    result = evaluate_paid_gmv(
        _trade(
            tid="1001",
            status="WAIT_BUYER_PAY",
            payment=None,
            pay_time=None,
        )
    )
    assert result is None


def test_paid_wait_seller_send_counts_gmv() -> None:
    """覆盖 paid wait seller send counts gmv"""
    result = evaluate_paid_gmv(
        _trade(
            tid="1002",
            status="WAIT_SELLER_SEND_GOODS",
            payment="199.00",
            pay_time="2026-07-01 10:00:00",
        )
    )
    assert result == PaidGmvContribution(
        tid="1002",
        payment_fen=19900,
        pay_date=date(2026, 7, 1),
        status="WAIT_SELLER_SEND_GOODS",
    )


def test_shipped_and_finished_count_gmv() -> None:
    """覆盖 shipped and finished count gmv"""
    shipped = evaluate_paid_gmv(
        _trade(
            tid="1003",
            status="WAIT_BUYER_CONFIRM_GOODS",
            payment="50.50",
            pay_time="2026-07-02 12:00:00",
        )
    )
    finished = evaluate_paid_gmv(
        _trade(
            tid="1004",
            status="TRADE_FINISHED",
            payment="10.00",
            pay_time="2026-07-03 08:00:00",
        )
    )
    assert shipped is not None and shipped.payment_fen == 5050
    assert finished is not None and finished.payment_fen == 1000


def test_closed_before_pay_not_in_gmv() -> None:
    """覆盖 closed before pay not in gmv"""
    result = evaluate_paid_gmv(
        _trade(
            tid="1005",
            status="TRADE_CLOSED_BY_TAOBAO",
            payment=None,
            pay_time=None,
        )
    )
    assert result is None


def test_trade_closed_after_pay_keeps_historical_gmv() -> None:
    """覆盖 trade closed after pay keeps historical gmv"""
    result = evaluate_paid_gmv(
        _trade(
            tid="1006",
            status="TRADE_CLOSED",
            payment="88.00",
            pay_time="2026-07-04 09:00:00",
        )
    )
    assert result is not None
    assert result.payment_fen == 8800


def test_multi_order_lines_do_not_double_count_main_gmv() -> None:
    """覆盖 multi order lines do not double count main gmv"""
    result = evaluate_paid_gmv(
        _trade(
            tid="1007",
            status="TRADE_FINISHED",
            payment="300.00",
            pay_time="2026-07-05 11:00:00",
            orders=[
                {"oid": "1", "payment": "100.00"},
                {"oid": "2", "payment": "200.00"},
            ],
        )
    )
    assert result is not None
    assert result.payment_fen == 30000
    aggregated = aggregate_paid_gmv_fen(
        [
            result,
            result,
        ]
    )
    assert aggregated == 30000


def test_pay_time_determines_stat_date() -> None:
    """覆盖 pay time determines stat date"""
    result = evaluate_paid_gmv(
        _trade(
            tid="1008",
            status="WAIT_SELLER_SEND_GOODS",
            payment="1.00",
            pay_time="2026-07-15 23:59:59",
        )
    )
    assert result is not None
    assert result.pay_date == date(2026, 7, 15)


def test_missing_payment_or_pay_time_unavailable() -> None:
    """覆盖 missing payment or pay time unavailable"""
    with pytest.raises(GmvUnavailableError) as exc:
        evaluate_paid_gmv(
            _trade(
                tid="1009",
                status="WAIT_SELLER_SEND_GOODS",
                payment=None,
                pay_time="2026-07-01 10:00:00",
            )
        )
    assert exc.value.code == "TAOBAO_GMV_UNAVAILABLE"

    with pytest.raises(GmvUnavailableError) as exc2:
        evaluate_paid_gmv(
            _trade(
                tid="1010",
                status="WAIT_SELLER_SEND_GOODS",
                payment="10.00",
                pay_time=None,
            )
        )
    assert exc2.value.code == "TAOBAO_GMV_UNAVAILABLE"


def test_received_payment_does_not_replace_paid_gmv() -> None:
    """覆盖 received payment does not replace paid gmv"""
    result = evaluate_paid_gmv(
        _trade(
            tid="1011",
            status="TRADE_FINISHED",
            payment="100.00",
            pay_time="2026-07-06 10:00:00",
            received_payment="80.00",
        )
    )
    assert result is not None
    assert result.payment_fen == 10000
