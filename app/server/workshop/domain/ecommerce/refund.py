from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Mapping

from app.server.workshop.domain.ecommerce.money import parse_top_yuan_to_fen

SUCCESS_STATUS = "SUCCESS"


@dataclass(frozen=True, slots=True)
class RefundSnapshot:
    refund_id: str
    refund_version: int
    status: str
    refund_fee_fen: int
    oid: str
    refund_date: date


@dataclass
class RefundStore:
    """内存退款分层，供 sync/TMC 测试与领域服务使用"""

    _events: list[dict[str, Any]] = field(default_factory=list)
    _snapshots: dict[str, RefundSnapshot] = field(default_factory=dict)
    _seen_versions: set[tuple[str, int]] = field(default_factory=set)

    def apply_event(self, event: Mapping[str, Any]) -> None:
        """应用退款事件并更新快照"""
        refund_id = str(event["refund_id"])
        version = int(event["refund_version"])
        key = (refund_id, version)
        # Record raw event always for audit layer.
        self._events.append(dict(event))
        if key in self._seen_versions:
            return
        self._seen_versions.add(key)

        current = self._snapshots.get(refund_id)
        if current is not None and version < current.refund_version:
            return
        if current is not None and version == current.refund_version:
            return

        fee_fen = parse_top_yuan_to_fen(str(event["refund_fee"]))
        refund_date = _refund_date(event)
        self._snapshots[refund_id] = RefundSnapshot(
            refund_id=refund_id,
            refund_version=version,
            status=str(event["status"]),
            refund_fee_fen=fee_fen,
            oid=str(event.get("oid") or ""),
            refund_date=refund_date,
        )

    def current_snapshot(self, refund_id: str) -> RefundSnapshot | None:
        """读取当前退款快照"""
        return self._snapshots.get(refund_id)

    def all_current_snapshots(self) -> list[RefundSnapshot]:
        """列出全部当前退款快照"""
        return list(self._snapshots.values())


def _refund_date(event: Mapping[str, Any]) -> date:
    """解析退款日期"""
    end_time = event.get("end_time")
    modified = event.get("modified")
    raw = end_time if end_time not in (None, "") else modified
    if raw is None or raw == "":
        raise ValueError("refund event missing end_time and modified")
    text = str(raw).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"invalid refund time: {text!r}")


def aggregate_success_refund_fee_fen(store: RefundStore) -> int:
    """财务视图：按 refund_id 取最新快照；仅 SUCCESS"""
    total = 0
    for snap in store.all_current_snapshots():
        if snap.status == SUCCESS_STATUS:
            total += snap.refund_fee_fen
    return total


def aggregate_success_refund_fee_fen_in_window(
    store: RefundStore,
    *,
    window_start: date,
    window_end: date,
) -> int:
    """窗口内汇总 SUCCESS 退款金额（分）"""
    total = 0
    for snap in store.all_current_snapshots():
        if snap.status != SUCCESS_STATUS:
            continue
        if window_start <= snap.refund_date <= window_end:
            total += snap.refund_fee_fen
    return total


def net_paid_after_refunds(
    *,
    paid_gmv_fen: int,
    refund_store: RefundStore,
    refund_window_start: date,
    refund_window_end: date,
) -> int:
    """实付扣除退款后的净额"""
    refunds = aggregate_success_refund_fee_fen_in_window(
        refund_store,
        window_start=refund_window_start,
        window_end=refund_window_end,
    )
    return paid_gmv_fen - refunds
