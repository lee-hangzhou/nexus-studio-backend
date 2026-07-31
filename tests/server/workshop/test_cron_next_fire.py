from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from app.server.workshop.domain.cron_next_fire import (
    CronNextFireError,
    InvalidCronExpressionError,
    InvalidTimezoneError,
    compute_next_run_at,
    schedule_trigger_key,
)


def test_compute_next_run_at_asia_shanghai_to_utc() -> None:
    """无 DST 时区：本地 09:00 映射到确定 UTC"""
    after = datetime(2026, 7, 30, 0, 0, tzinfo=timezone.utc)
    next_at = compute_next_run_at(
        cron="0 9 * * *",
        timezone_name="Asia/Shanghai",
        after=after,
    )
    assert next_at == datetime(2026, 7, 30, 1, 0, tzinfo=timezone.utc)
    assert next_at.tzinfo is not None


def test_compute_next_run_at_america_new_york_dst_spring_forward() -> None:
    """春令时跳变后，同刻本地触发的 UTC 偏移随 DST 变化"""
    ny = ZoneInfo("America/New_York")
    before = datetime(2026, 3, 8, 1, 0, tzinfo=ny)
    spring_slot = compute_next_run_at(
        cron="30 2 * * *",
        timezone_name="America/New_York",
        after=before,
    )
    # CronTrigger 在春令跳变日仍给出 02:30-05:00 → UTC 07:30
    assert spring_slot == datetime(2026, 3, 8, 7, 30, tzinfo=timezone.utc)

    after_gap = datetime(2026, 3, 8, 3, 0, tzinfo=ny)
    next_day = compute_next_run_at(
        cron="30 2 * * *",
        timezone_name="America/New_York",
        after=after_gap,
    )
    # 次日已进入 EDT：02:30-04:00 → UTC 06:30
    assert next_day == datetime(2026, 3, 9, 6, 30, tzinfo=timezone.utc)


def test_compute_next_run_at_america_new_york_dst_fall_back() -> None:
    """秋令时回拨：第一次 01:30 与第二次折叠区间后的下一火不同 UTC"""
    ny = ZoneInfo("America/New_York")
    before = datetime(2026, 11, 1, 0, 30, tzinfo=ny)
    first = compute_next_run_at(
        cron="30 1 * * *",
        timezone_name="America/New_York",
        after=before,
    )
    assert first == datetime(2026, 11, 1, 5, 30, tzinfo=timezone.utc)

    after_first = datetime(2026, 11, 1, 1, 31, fold=0, tzinfo=ny)
    second = compute_next_run_at(
        cron="30 1 * * *",
        timezone_name="America/New_York",
        after=after_first,
    )
    assert second == datetime(2026, 11, 1, 6, 30, tzinfo=timezone.utc)


def test_compute_next_run_at_exclusive_after_previous_fire() -> None:
    """claim 推进：previous_fire_time 与 after 同为到期点时跳到下一火"""
    due = datetime(2026, 7, 30, 9, 0, tzinfo=timezone.utc)
    next_at = compute_next_run_at(
        cron="0 9 * * *",
        timezone_name="UTC",
        after=due,
        previous_fire_time=due,
    )
    assert next_at == datetime(2026, 7, 31, 9, 0, tzinfo=timezone.utc)


def test_compute_next_run_at_rejects_invalid_cron() -> None:
    """非法 cron 显式失败，无兜底"""
    after = datetime(2026, 7, 30, 0, 0, tzinfo=timezone.utc)
    with pytest.raises(InvalidCronExpressionError):
        compute_next_run_at(cron="0 9 *", timezone_name="UTC", after=after)
    with pytest.raises(InvalidCronExpressionError):
        compute_next_run_at(cron="99 9 * * *", timezone_name="UTC", after=after)
    with pytest.raises(InvalidCronExpressionError):
        compute_next_run_at(cron="", timezone_name="UTC", after=after)


def test_compute_next_run_at_rejects_invalid_timezone() -> None:
    """非法时区显式失败"""
    after = datetime(2026, 7, 30, 0, 0, tzinfo=timezone.utc)
    with pytest.raises(InvalidTimezoneError):
        compute_next_run_at(cron="0 9 * * *", timezone_name="Not/AZone", after=after)
    with pytest.raises(InvalidTimezoneError):
        compute_next_run_at(cron="0 9 * * *", timezone_name="  ", after=after)


def test_compute_next_run_at_rejects_naive_datetime() -> None:
    """拒绝 naive datetime"""
    with pytest.raises(CronNextFireError, match="timezone-aware"):
        compute_next_run_at(
            cron="0 9 * * *",
            timezone_name="UTC",
            after=datetime(2026, 7, 30, 0, 0),
        )


def test_schedule_trigger_key_deterministic_utc() -> None:
    """trigger_key 由 schedule id 与 due UTC 决定"""
    due = datetime(2026, 7, 30, 9, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    key = schedule_trigger_key(schedule_id="sched_abc", due_fire_at=due)
    assert key == "sched_abc:20260730T010000Z"
    same = schedule_trigger_key(
        schedule_id="sched_abc",
        due_fire_at=datetime(2026, 7, 30, 1, 0, tzinfo=timezone.utc),
    )
    assert same == key
