from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable

from app.agent.canvas.turn.lock import canvas_turn_lock
from app.agent.runtime.stream.frames import StreamFrameType
from app.agent.runtime.stream.replay import (
    ReplayExecutionClosed,
    ReplayFramePublisher,
    ReplayLogLimitExceeded,
    ReplayStatus,
    decode_encoded_sse_frame,
    new_lease_owner,
    replay_store,
)
from app.server.chat.domain.stream_enums import StreamErrorCode
from app.server.infra.config import settings
from app.server.infra.logger import logger

ReplayStreamFactory = Callable[[], AsyncIterator[str]]


class ExecutionLeaseLost(RuntimeError):
    pass


async def run_canvas_replay_execution(
    *,
    request_id: str,
    project_id: int,
    episode_id: int,
    turn_id: str,
    cancel_event: asyncio.Event,
    stream_factory: ReplayStreamFactory,
) -> None:
    """独立于 HTTP 连接执行 Canvas turn，并将业务帧写入 Redis replay log。"""
    lease_owner = new_lease_owner()
    publisher = ReplayFramePublisher(replay_store, request_id)
    lease_task: asyncio.Task[None] | None = None
    cancel_task: asyncio.Task[None] | None = None
    lease_lost_event = asyncio.Event()
    final_status = ReplayStatus.FAILED

    try:
        await replay_store.set_running(request_id, lease_owner=lease_owner)
        lease_task = asyncio.create_task(
            _refresh_lease(
                request_id=request_id,
                lease_owner=lease_owner,
                project_id=project_id,
                episode_id=episode_id,
                turn_id=turn_id,
                cancel_event=cancel_event,
                lease_lost_event=lease_lost_event,
            )
        )
        cancel_task = asyncio.create_task(
            _watch_cancel(episode_id=episode_id, turn_id=turn_id, cancel_event=cancel_event)
        )

        async for chunk in stream_factory():
            if lease_lost_event.is_set():
                raise ExecutionLeaseLost("stream execution lease was lost")
            frame = decode_encoded_sse_frame(chunk)
            if frame.type == StreamFrameType.HEARTBEAT:
                continue
            await publisher.publish(frame)

        if lease_lost_event.is_set():
            raise ExecutionLeaseLost("stream execution lease was lost")

        await publisher.close()
        if publisher.terminal_status is not None:
            final_status = publisher.terminal_status
        elif publisher.saw_pending:
            final_status = ReplayStatus.INTERRUPTED
        else:
            await publisher.append_failure("Agent stream ended without a terminal frame")
            final_status = ReplayStatus.FAILED
    except ReplayLogLimitExceeded:
        cancel_event.set()
        await publisher.append_failure("SSE replay log exceeded the configured size limit")
        final_status = ReplayStatus.FAILED
    except ExecutionLeaseLost:
        await publisher.append_failure("Agent execution was lost", code=StreamErrorCode.EXECUTION_LOST)
        final_status = ReplayStatus.FAILED
    except ReplayExecutionClosed:
        cancel_event.set()
        final_status = ReplayStatus.FAILED
    except asyncio.CancelledError:
        try:
            await publisher.append_failure("Agent execution was lost", code=StreamErrorCode.EXECUTION_LOST)
            final_status = ReplayStatus.FAILED
        except Exception as exc:
            logger.exception(
                "canvas.turn.replay_shutdown_frame_failed",
                request_id=request_id,
                turn_id=turn_id,
                error=str(exc),
            )
        raise
    except Exception as exc:
        logger.exception(
            "canvas.turn.replay_execution_failed",
            request_id=request_id,
            turn_id=turn_id,
            error=str(exc),
        )
        await publisher.append_failure(str(exc))
        final_status = ReplayStatus.FAILED
    finally:
        for task in (lease_task, cancel_task):
            if task is not None:
                task.cancel()
        tasks = [task for task in (lease_task, cancel_task) if task is not None]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        try:
            await replay_store.finish(request_id, final_status)
        finally:
            try:
                await replay_store.clear_cancel(session_id=episode_id, turn_id=turn_id)
            finally:
                await canvas_turn_lock.release(episode_id, turn_id)
        logger.info(
            "canvas.turn.replay_finished",
            request_id=request_id,
            turn_id=turn_id,
            status=final_status.value,
        )


async def _refresh_lease(
    *,
    request_id: str,
    lease_owner: str,
    project_id: int,
    episode_id: int,
    turn_id: str,
    cancel_event: asyncio.Event,
    lease_lost_event: asyncio.Event,
) -> None:
    while not cancel_event.is_set():
        await asyncio.sleep(settings.SSE_EXECUTION_LEASE_REFRESH_SEC)
        try:
            refreshed = await replay_store.refresh_lease(request_id, lease_owner=lease_owner)
        except Exception as exc:
            logger.exception("canvas.turn.replay_lease_refresh_failed", request_id=request_id, error=str(exc))
            lease_lost_event.set()
            cancel_event.set()
            return
        if not refreshed:
            logger.error("canvas.turn.replay_lease_lost", request_id=request_id)
            lease_lost_event.set()
            cancel_event.set()
            return
        try:
            lock_refreshed = await canvas_turn_lock.refresh(episode_id, turn_id)
        except Exception as exc:
            logger.exception(
                "canvas.turn.episode_lock_refresh_failed",
                request_id=request_id,
                project_id=project_id,
                episode_id=episode_id,
                turn_id=turn_id,
                error=str(exc),
            )
            lease_lost_event.set()
            cancel_event.set()
            return
        if not lock_refreshed:
            logger.error(
                "canvas.turn.episode_lock_lost",
                request_id=request_id,
                project_id=project_id,
                episode_id=episode_id,
                turn_id=turn_id,
            )
            lease_lost_event.set()
            cancel_event.set()
            return


async def _watch_cancel(*, episode_id: int, turn_id: str, cancel_event: asyncio.Event) -> None:
    while not cancel_event.is_set():
        if await replay_store.is_cancel_requested(session_id=episode_id, turn_id=turn_id):
            cancel_event.set()
            return
        await asyncio.sleep(0.25)
