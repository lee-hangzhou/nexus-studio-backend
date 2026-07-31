from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Callable, Optional

from app.server.infra.logger import logger
from app.server.workshop.services.workflow_schedule_service import (
    WorkshopWorkflowScheduleService,
)


async def run_workshop_schedule_ticker(
    *,
    schedule_service: WorkshopWorkflowScheduleService,
    owner: str,
    tick_interval_sec: float,
    batch_size: int,
    deadline_sec: float,
    clock: Optional[Callable[[], datetime]] = None,
) -> None:
    """长驻异步 tick 循环：显式 owner、间隔、批量与每 tick deadline"""
    if not owner.strip():
        raise ValueError("schedule ticker owner required")
    if tick_interval_sec <= 0:
        raise ValueError("tick_interval_sec must be > 0")
    if batch_size < 1:
        raise ValueError("batch_size must be >= 1")
    if deadline_sec <= 0:
        raise ValueError("deadline_sec must be > 0")

    def _now() -> datetime:
        """读取当前 UTC 时刻"""
        if clock is None:
            return datetime.now(timezone.utc)
        return clock()

    owner_name = owner.strip()
    logger.info(
        "workshop.schedule_ticker.started",
        owner=owner_name,
        tick_interval_sec=tick_interval_sec,
        batch_size=batch_size,
        deadline_sec=deadline_sec,
    )
    try:
        while True:
            tick_started = _now()
            try:
                async with asyncio.timeout(deadline_sec):
                    claimed = await schedule_service.tick_once(
                        now=tick_started,
                        batch_size=batch_size,
                    )
                logger.info(
                    "workshop.schedule_ticker.tick",
                    owner=owner_name,
                    claimed=len(claimed),
                    now=tick_started.isoformat(),
                )
            except TimeoutError:
                logger.error(
                    "workshop.schedule_ticker.deadline_exceeded",
                    owner=owner_name,
                    deadline_sec=deadline_sec,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error(
                    "workshop.schedule_ticker.tick_failed",
                    owner=owner_name,
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
            await asyncio.sleep(tick_interval_sec)
    except asyncio.CancelledError:
        logger.info("workshop.schedule_ticker.cancelled", owner=owner_name)
        raise
