from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from apscheduler.triggers.cron import CronTrigger


class InvalidCronExpressionError(ValueError):
    """非法五段 cron 表达式"""


class InvalidTimezoneError(ValueError):
    """非法 IANA 时区名"""


class CronNextFireError(ValueError):
    """无法计算下一次触发时间"""


def _require_aware(moment: datetime, *, field_name: str) -> datetime:
    """要求带时区的 datetime"""
    if moment.tzinfo is None:
        raise CronNextFireError(f"{field_name} must be timezone-aware")
    return moment


def _parse_iana_timezone(timezone_name: str) -> ZoneInfo:
    """解析 IANA 时区；非法则显式失败"""
    name = timezone_name.strip()
    if not name:
        raise InvalidTimezoneError("timezone required")
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise InvalidTimezoneError(f"unknown IANA timezone: {name}") from exc


def _parse_five_field_cron(*, cron: str, tz: ZoneInfo) -> CronTrigger:
    """解析严格五段 cron 为 CronTrigger；不做兜底"""
    expression = cron.strip()
    if not expression:
        raise InvalidCronExpressionError("cron required")
    fields = expression.split()
    if len(fields) != 5:
        raise InvalidCronExpressionError(
            f"cron must have exactly 5 fields; got {len(fields)}"
        )
    try:
        return CronTrigger.from_crontab(expression, timezone=tz)
    except ValueError as exc:
        raise InvalidCronExpressionError(f"invalid cron: {expression}") from exc


def schedule_trigger_key(*, schedule_id: str, due_fire_at: datetime) -> str:
    """由 schedule id 与到期触发 UTC 时刻生成确定性 trigger_key"""
    schedule = schedule_id.strip()
    if not schedule:
        raise CronNextFireError("schedule_id required")
    due_utc = _require_aware(due_fire_at, field_name="due_fire_at").astimezone(
        timezone.utc
    )
    return f"{schedule}:{due_utc.strftime('%Y%m%dT%H%M%SZ')}"


def compute_next_run_at(
    *,
    cron: str,
    timezone_name: str,
    after: datetime,
    previous_fire_time: datetime | None = None,
) -> datetime:
    """用 APScheduler CronTrigger 纯计算下一次触发并返回 aware UTC"""
    after_aware = _require_aware(after, field_name="after")
    previous: datetime | None = None
    if previous_fire_time is not None:
        previous = _require_aware(previous_fire_time, field_name="previous_fire_time")
    tz = _parse_iana_timezone(timezone_name)
    trigger = _parse_five_field_cron(cron=cron, tz=tz)
    next_fire = trigger.get_next_fire_time(previous, after_aware)
    if next_fire is None:
        raise CronNextFireError("cron has no next fire time")
    if not isinstance(next_fire, datetime):
        raise CronNextFireError("cron next fire must be datetime")
    if next_fire.tzinfo is None:
        raise CronNextFireError("cron next fire missing timezone")
    return next_fire.astimezone(timezone.utc)
