from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator

from app.agent.runtime.stream.encoder import encode_sse_frame
from app.agent.runtime.stream.frames import StreamFrameType, create_stream_frame
from app.server.chat.domain.stream_enums import StreamErrorCode
from app.server.infra.config import settings

STREAM_END_SENTINEL: None = None


async def pump_encoded_sse_queue(
    out: asyncio.Queue[str | None],
    *,
    turn_id: str,
    cancel_event: asyncio.Event | None = None,
    deadline: float | None = None,
    heartbeat_interval_sec: int | None = None,
) -> AsyncIterator[str]:
    cancel_wait = asyncio.create_task(cancel_event.wait()) if cancel_event is not None else None
    heartbeat_timeout = float(
        heartbeat_interval_sec
        if heartbeat_interval_sec is not None
        else settings.CHAT_HEARTBEAT_INTERVAL_SEC
    )
    try:
        while True:
            if cancel_event is not None and cancel_event.is_set():
                yield encode_sse_frame(
                    create_stream_frame(
                        type=StreamFrameType.CANCELLED,
                        turn_id=turn_id,
                        reason="user_cancel",
                    )
                )
                break

            now = time.monotonic()
            if deadline is not None and now > deadline:
                yield encode_sse_frame(
                    create_stream_frame(
                        type=StreamFrameType.ERROR,
                        code=StreamErrorCode.GENERATION_TIMEOUT,
                        message="节点生成等待超时",
                        turn_id=turn_id,
                    )
                )
                yield encode_sse_frame(create_stream_frame(type=StreamFrameType.DONE, turn_id=turn_id))
                break

            timeout = heartbeat_timeout
            if deadline is not None:
                timeout = min(timeout, max(0.0, deadline - now))

            queue_wait = asyncio.create_task(out.get())
            waiters = {queue_wait}
            if cancel_wait is not None:
                waiters.add(cancel_wait)
            try:
                done, _ = await asyncio.wait(
                    waiters,
                    timeout=timeout,
                    return_when=asyncio.FIRST_COMPLETED,
                )
            finally:
                if not queue_wait.done():
                    queue_wait.cancel()
                    await asyncio.gather(queue_wait, return_exceptions=True)

            if cancel_wait is not None and cancel_wait in done:
                yield encode_sse_frame(
                    create_stream_frame(
                        type=StreamFrameType.CANCELLED,
                        turn_id=turn_id,
                        reason="user_cancel",
                    )
                )
                break
            if queue_wait in done:
                item = queue_wait.result()
                if item is STREAM_END_SENTINEL:
                    break
                yield item
                continue

            if deadline is not None and time.monotonic() >= deadline:
                yield encode_sse_frame(
                    create_stream_frame(
                        type=StreamFrameType.ERROR,
                        code=StreamErrorCode.GENERATION_TIMEOUT,
                        message="节点生成等待超时",
                        turn_id=turn_id,
                    )
                )
                yield encode_sse_frame(create_stream_frame(type=StreamFrameType.DONE, turn_id=turn_id))
                break

            yield encode_sse_frame(
                create_stream_frame(
                    type=StreamFrameType.HEARTBEAT,
                    turn_id=turn_id,
                    ts=int(time.time()),
                )
            )
    finally:
        if cancel_wait is not None and not cancel_wait.done():
            cancel_wait.cancel()
            await asyncio.gather(cancel_wait, return_exceptions=True)
