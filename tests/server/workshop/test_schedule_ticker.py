from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import List
from unittest.mock import AsyncMock

import pytest

from app.server.infra.config import Settings
from app.server.workshop.services.schedule_ticker import run_workshop_schedule_ticker
from app.server.workshop.services.workflow_schedule_service import ScheduleTriggerResult


@pytest.mark.asyncio
async def test_schedule_ticker_propagates_cancellation() -> None:
    """ticker 在取消时退出并传播 CancelledError"""
    service = AsyncMock()
    service.tick_once = AsyncMock(return_value=())

    task = asyncio.create_task(
        run_workshop_schedule_ticker(
            schedule_service=service,
            owner="test-owner",
            tick_interval_sec=0.05,
            batch_size=8,
            deadline_sec=0.04,
            clock=lambda: datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc),
        )
    )
    await asyncio.sleep(0.02)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert service.tick_once.await_count >= 1


@pytest.mark.asyncio
async def test_schedule_ticker_deadline_does_not_stop_loop() -> None:
    """单 tick 超时记录后继续循环，取消才退出"""
    calls: List[int] = []

    async def slow_tick(*, now: datetime, batch_size: int) -> tuple[ScheduleTriggerResult, ...]:
        """故意超过 deadline 的 tick"""
        _ = now, batch_size
        calls.append(1)
        await asyncio.sleep(0.05)
        return ()

    service = AsyncMock()
    service.tick_once = AsyncMock(side_effect=slow_tick)

    task = asyncio.create_task(
        run_workshop_schedule_ticker(
            schedule_service=service,
            owner="test-owner",
            tick_interval_sec=0.01,
            batch_size=4,
            deadline_sec=0.01,
            clock=lambda: datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc),
        )
    )
    await asyncio.sleep(0.08)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(calls) >= 2


def test_workshop_schedule_ticker_settings_reject_dangerous_values() -> None:
    """ticker 配置：deadline 必须短于间隔；生产拒绝过激进参数"""
    with pytest.raises(ValueError, match="DEADLINE_SEC must be <"):
        Settings(
            WORKSHOP_SCHEDULE_TICK_INTERVAL_SEC=10,
            WORKSHOP_SCHEDULE_TICK_DEADLINE_SEC=10,
        )
    with pytest.raises(ValueError, match=">= 5 in production"):
        Settings(
            ENV="prod",
            WORKSHOP_SCHEDULE_TICK_INTERVAL_SEC=2,
            WORKSHOP_SCHEDULE_TICK_DEADLINE_SEC=1,
        )
    with pytest.raises(ValueError, match="<= 200 in production"):
        Settings(
            ENV="production",
            WORKSHOP_SCHEDULE_TICK_INTERVAL_SEC=30,
            WORKSHOP_SCHEDULE_TICK_DEADLINE_SEC=10,
            WORKSHOP_SCHEDULE_TICK_BATCH_SIZE=201,
        )
    ok = Settings(
        WORKSHOP_SCHEDULE_TICKER_ENABLED=True,
        WORKSHOP_SCHEDULE_TICK_INTERVAL_SEC=30,
        WORKSHOP_SCHEDULE_TICK_DEADLINE_SEC=20,
        WORKSHOP_SCHEDULE_TICK_BATCH_SIZE=32,
    )
    assert ok.WORKSHOP_SCHEDULE_TICKER_ENABLED is True


def test_schedule_ticker_is_enabled_by_default() -> None:
    """工坊定时调度默认启用，避免已创建定时静默不执行"""
    assert Settings().WORKSHOP_SCHEDULE_TICKER_ENABLED is True


def test_lifespan_starts_ticker_after_checkpointer_and_memory_store() -> None:
    """ticker 仅在 checkpointer+memory store 成功初始化后启动，并由 supervisor 关闭"""
    from pathlib import Path

    source = Path("app/server/infra/lifespan.py").read_text(encoding="utf-8")
    cp_idx = source.index("async with create_checkpointer()")
    mem_idx = source.index("create_memory_store()")
    ticker_idx = source.index('name="workshop.schedule_ticker"')
    close_idx = source.index("await background_supervisor.close()")
    assert mem_idx > cp_idx - 80
    assert ticker_idx > source.index("set_memory_store(memory_store)")
    assert close_idx > ticker_idx
    # 启动失败路径不应在 checkpointer 之前启动 ticker
    pre_cp = source[:cp_idx]
    assert "workshop.schedule_ticker" not in pre_cp
