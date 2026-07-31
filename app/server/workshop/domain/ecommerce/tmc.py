from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Mapping, Protocol

from app.server.workshop.domain.ecommerce.refund import RefundStore

V1_TOPICS: frozenset[str] = frozenset(
    {
        "taobao_trade_TradeBuyerPay",
        "taobao_trade_TradeSellerShip",
        "taobao_trade_TradeSuccess",
        "taobao_trade_TradeClose",
        "taobao_trade_TradeChanged",
        "taobao_trade_TradePartlyRefund",
        "taobao_refund_RefundCreated",
        "taobao_refund_RefundSuccess",
        "taobao_refund_RefundClosed",
        "taobao_item_ItemAdd",
        "taobao_item_ItemUpdate",
        "taobao_item_ItemUpshelf",
        "taobao_item_ItemDownshelf",
        "taobao_item_ItemDelete",
    }
)


class TmcTopicKind(str, Enum):
    TRADE = "trade"
    REFUND = "refund"
    ITEM = "item"


@dataclass(frozen=True, slots=True)
class TmcMessage:
    msg_id: str
    topic: str
    content: Mapping[str, Any]


@dataclass
class TmcHandleResult:
    confirmed: bool
    reason: str | None = None
    business_key: str | None = None


class RestRefreshPort(Protocol):
    def refresh_trade(self, tid: str) -> Mapping[str, Any]:
        """REST 刷新交易"""
        ...

    def refresh_refund(self, refund_id: str) -> Mapping[str, Any]:
        """REST 刷新退款"""
        ...

    def refresh_item(self, num_iid: str) -> Mapping[str, Any]:
        """REST 刷新商品"""
        ...


@dataclass
class RecordingRestPort:
    """测试用假 REST 刷新端口"""

    trades: dict[str, dict[str, Any]] = field(default_factory=dict)
    refunds: dict[str, dict[str, Any]] = field(default_factory=dict)
    items: dict[str, dict[str, Any]] = field(default_factory=dict)
    trade_calls: list[str] = field(default_factory=list)
    refund_calls: list[str] = field(default_factory=list)
    item_calls: list[str] = field(default_factory=list)
    fail_trade: set[str] = field(default_factory=set)

    def refresh_trade(self, tid: str) -> Mapping[str, Any]:
        """REST 刷新交易"""
        self.trade_calls.append(tid)
        if tid in self.fail_trade:
            raise RuntimeError("rest trade refresh failed")
        return self.trades.get(tid, {"tid": tid, "status": "WAIT_SELLER_SEND_GOODS"})

    def refresh_refund(self, refund_id: str) -> Mapping[str, Any]:
        """REST 刷新退款"""
        self.refund_calls.append(refund_id)
        return self.refunds[refund_id]

    def refresh_item(self, num_iid: str) -> Mapping[str, Any]:
        """REST 刷新商品"""
        self.item_calls.append(num_iid)
        return self.items.get(num_iid, {"num_iid": num_iid})


@dataclass
class TmcAccelerator:
    """可选 TMC 加速器；仅 REST 刷新成功后确认；msgId 与业务键双幂等；不作唯一事实源"""

    rest: RestRefreshPort
    refund_store: RefundStore
    _seen_msg_ids: set[str] = field(default_factory=set)
    _seen_business_keys: set[str] = field(default_factory=set)
    confirmed_msg_ids: list[str] = field(default_factory=list)
    trade_snapshots: dict[str, Mapping[str, Any]] = field(default_factory=dict)
    item_snapshots: dict[str, Mapping[str, Any]] = field(default_factory=dict)

    def handle(self, message: TmcMessage) -> TmcHandleResult:
        """处理单条 TMC 消息"""
        if message.topic not in V1_TOPICS:
            return TmcHandleResult(confirmed=False, reason="unsupported_topic")

        business_key = _business_key(message)
        if message.msg_id in self._seen_msg_ids or business_key in self._seen_business_keys:
            # Duplicate delivery: confirm to stop redelivery without re-applying.
            self.confirmed_msg_ids.append(message.msg_id)
            self._seen_msg_ids.add(message.msg_id)
            return TmcHandleResult(
                confirmed=True, reason="duplicate", business_key=business_key
            )

        try:
            kind = _topic_kind(message.topic)
            if kind == TmcTopicKind.TRADE:
                tid = str(message.content["tid"])
                snap = self.rest.refresh_trade(tid)
                self.trade_snapshots[tid] = snap
            elif kind == TmcTopicKind.REFUND:
                refund_id = str(message.content["refund_id"])
                snap = self.rest.refresh_refund(refund_id)
                # Always go through refund_version snapshot logic — never bypass.
                self.refund_store.apply_event(snap)
            else:
                num_iid = str(message.content["num_iid"])
                snap = self.rest.refresh_item(num_iid)
                self.item_snapshots[num_iid] = snap
        except Exception as exc:  # noqa: BLE001 — fail closed: do not confirm
            return TmcHandleResult(
                confirmed=False, reason=f"handler_error:{exc}", business_key=business_key
            )

        self._seen_msg_ids.add(message.msg_id)
        self._seen_business_keys.add(business_key)
        self.confirmed_msg_ids.append(message.msg_id)
        return TmcHandleResult(confirmed=True, business_key=business_key)

    def rest_compensate_trades(self, tids: list[str]) -> None:
        """独立 REST 增量补偿（必选）"""
        for tid in tids:
            self.trade_snapshots[tid] = self.rest.refresh_trade(tid)

    def rest_compensate_refunds(self, refund_payloads: list[Mapping[str, Any]]) -> None:
        """REST 补偿退款增量"""
        for payload in refund_payloads:
            self.refund_store.apply_event(payload)


def _topic_kind(topic: str) -> TmcTopicKind:
    """解析 TMC topic 类型"""
    if topic.startswith("taobao_trade_"):
        return TmcTopicKind.TRADE
    if topic.startswith("taobao_refund_"):
        return TmcTopicKind.REFUND
    return TmcTopicKind.ITEM


def _business_key(message: TmcMessage) -> str:
    """解析业务主键"""
    c = message.content
    if message.topic.startswith("taobao_refund_"):
        return f"refund:{c.get('refund_id')}"
    if message.topic.startswith("taobao_item_"):
        return f"item:{c.get('num_iid')}:{message.topic}"
    # trade
    return f"trade:{c.get('tid')}:{message.topic}"
