from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Iterable, Mapping

from app.server.workshop.domain.ecommerce.money import MoneyParseError, parse_top_yuan_to_fen

# Historical paid GMV whitelist.
PAID_GMV_STATUS_WHITELIST: frozenset[str] = frozenset(
    {
        "WAIT_SELLER_SEND_GOODS",
        "PAID_FORBID_CONSIGN",
        "SELLER_CONSIGNED_PART",
        "WAIT_BUYER_CONFIRM_GOODS",
        "TRADE_BUYER_SIGNED",
        "TRADE_FINISHED",
        "TRADE_CLOSED",
    }
)

# Never count as paid GMV.
PAID_GMV_STATUS_EXCLUDE: frozenset[str] = frozenset(
    {
        "TRADE_NO_CREATE_PAY",
        "WAIT_BUYER_PAY",
        "TRADE_CLOSED_BY_TAOBAO",
        "PAY_PENDING",
        "WAIT_PRE_AUTH_CONFIRM",
    }
)


class GmvUnavailableError(Exception):
    """白名单状态但缺少 payment/pay_time；禁止静默记零"""

    def __init__(self, code: str, message: str) -> None:
        """初始化"""
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class PaidGmvContribution:
    tid: str
    payment_fen: int
    pay_date: date
    status: str


def _parse_pay_date(pay_time: str) -> date:
    """解析 pay_time 为日期"""
    text = pay_time.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise GmvUnavailableError("TAOBAO_GMV_UNAVAILABLE", f"invalid pay_time: {pay_time!r}")


def evaluate_paid_gmv(trade: Mapping[str, Any]) -> PaidGmvContribution | None:
    """判定主单实付 GMV；排除/未付返回 None；白名单缺字段则抛错；子单不计 GMV 金额"""
    status = str(trade.get("status") or "")
    if status in PAID_GMV_STATUS_EXCLUDE or status not in PAID_GMV_STATUS_WHITELIST:
        return None

    payment = trade.get("payment")
    pay_time = trade.get("pay_time")
    if payment is None or (isinstance(payment, str) and payment.strip() == ""):
        raise GmvUnavailableError(
            "TAOBAO_GMV_UNAVAILABLE",
            "whitelist trade missing payment",
        )
    if pay_time is None or (isinstance(pay_time, str) and pay_time.strip() == ""):
        raise GmvUnavailableError(
            "TAOBAO_GMV_UNAVAILABLE",
            "whitelist trade missing pay_time",
        )

    tid = str(trade.get("tid") or "").strip()
    if not tid:
        raise GmvUnavailableError("TAOBAO_GMV_UNAVAILABLE", "missing tid")

    try:
        payment_fen = parse_top_yuan_to_fen(str(payment))
    except MoneyParseError as exc:
        raise GmvUnavailableError("TAOBAO_GMV_UNAVAILABLE", str(exc)) from exc

    return PaidGmvContribution(
        tid=tid,
        payment_fen=payment_fen,
        pay_date=_parse_pay_date(str(pay_time)),
        status=status,
    )


def aggregate_paid_gmv_fen(contributions: Iterable[PaidGmvContribution]) -> int:
    """按唯一 tid 汇总实付 GMV（主单去重）"""
    by_tid: dict[str, PaidGmvContribution] = {}
    for item in contributions:
        by_tid[item.tid] = item
    return sum(c.payment_fen for c in by_tid.values())
