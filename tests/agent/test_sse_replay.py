from __future__ import annotations

import pytest
from pydantic import BaseModel, Field
from redis.exceptions import TimeoutError as RedisTimeoutError

from app.agent.runtime.stream.frames import StreamFrame, StreamFrameType, create_stream_frame
from app.agent.runtime.stream.replay import (
    ReplayFramePublisher,
    ReplayMeta,
    ReplayRequestKind,
    ReplayStatus,
    ReplayStore,
    build_request_fingerprint,
    decode_encoded_sse_frame,
    stream_replay,
    validate_event_id,
    validate_replay_cursor,
)
from app.server.chat.domain.stream_enums import StreamErrorCode, TokenChannel
from app.server.exceptions.base import AppError
from app.server.exceptions.codes import ErrorCode
from app.server.infra.config import settings


class _Request(BaseModel):
    request_id: str
    content: str
    options: dict[str, int] = Field(default_factory=dict)


class _FakeStore:
    def __init__(self, meta: ReplayMeta | None = None) -> None:
        self.frames: list[StreamFrame] = []
        self.close_statuses: list[ReplayStatus | None] = []
        self.meta = meta
        self._read_count = 0
        self.active_subscribers = 0

    async def register_subscriber(self) -> int:
        self.active_subscribers += 1
        return self.active_subscribers

    async def unregister_subscriber(self) -> int:
        self.active_subscribers -= 1
        return self.active_subscribers

    async def append_frame(
        self,
        _request_id: str,
        frame: StreamFrame,
        *,
        force: bool = False,
        close_status: ReplayStatus | None = None,
    ) -> str:
        _ = force
        self.frames.append(frame)
        self.close_statuses.append(close_status)
        return f"1-{len(self.frames)}"

    async def read_after(
        self,
        _request_id: str,
        _event_id: str,
        *,
        block_ms: int,
        count: int = 100,
    ) -> list[tuple[str, str]]:
        _ = block_ms, count
        self._read_count += 1
        if self._read_count == 1:
            return [
                ("10-2", '{"type":"token","protocol_version":2,"channel":"answer","text":"后续"}'),
                ("10-3", '{"type":"done","protocol_version":2,"turn_id":"turn-1","message_ids":[]}'),
            ]
        return []

    async def require_meta(self, _request_id: str) -> ReplayMeta:
        assert self.meta is not None
        return self.meta

    async def has_lease(self, _request_id: str) -> bool:
        return True


class _TimeoutThenFramesStore(_FakeStore):
    async def read_after(
        self,
        _request_id: str,
        _event_id: str,
        *,
        block_ms: int,
        count: int = 100,
    ) -> list[tuple[str, str]]:
        _ = block_ms, count
        self._read_count += 1
        if self._read_count == 1:
            raise RedisTimeoutError("subscriber read timed out")
        if self._read_count == 2:
            return [
                ("10-2", '{"type":"token","protocol_version":2,"channel":"answer","text":"恢复"}'),
                ("10-3", '{"type":"done","protocol_version":2,"turn_id":"turn-1","message_ids":[]}'),
            ]
        return []


class _ExistsClient:
    def __init__(self, exists: bool) -> None:
        self._exists = exists

    async def exists(self, _key: str) -> int:
        return int(self._exists)


class _RoutingControlClient:
    def __init__(self) -> None:
        self.set_calls: list[tuple[str, str, int | None]] = []

    async def set(self, key: str, value: str, *, ex: int | None = None) -> None:
        self.set_calls.append((key, value, ex))


class _RoutingSubscriberClient:
    def __init__(self) -> None:
        self.xread_calls: list[tuple[dict[str, str], int, int]] = []

    async def xread(self, streams: dict[str, str], *, count: int, block: int) -> list:
        self.xread_calls.append((streams, count, block))
        return []


def _meta(*, status: ReplayStatus, last_event_id: str | None) -> ReplayMeta:
    return ReplayMeta(
        request_id="f52b6fa7-792d-4b89-89d7-acde44a75a43",
        user_id=1,
        session_id=2,
        turn_id="turn-1",
        kind=ReplayRequestKind.TURN,
        fingerprint="fingerprint",
        status=status,
        last_event_id=last_event_id,
        bytes_written=0,
        created_at="2026-07-17T00:00:00+00:00",
        finished_at="2026-07-17T00:00:01+00:00" if status == ReplayStatus.COMPLETED else None,
    )


def test_request_fingerprint_ignores_request_id_and_sorts_object_keys() -> None:
    first = _Request(request_id="one", content="hello", options={"b": 2, "a": 1})
    second = _Request(request_id="two", content="hello", options={"a": 1, "b": 2})

    first_hash = build_request_fingerprint(
        kind=ReplayRequestKind.TURN,
        user_id=1,
        session_id=2,
        body=first,
    )
    second_hash = build_request_fingerprint(
        kind=ReplayRequestKind.TURN,
        user_id=1,
        session_id=2,
        body=second,
    )

    assert first_hash == second_hash
    assert first_hash != build_request_fingerprint(
        kind=ReplayRequestKind.RESUME,
        user_id=1,
        session_id=2,
        body=second,
    )


def test_validate_event_id_rejects_non_stream_cursor() -> None:
    assert validate_event_id(None) == "0-0"
    assert validate_event_id("10-2") == "10-2"
    with pytest.raises(AppError):
        validate_event_id("not-an-id")
    with pytest.raises(AppError):
        validate_replay_cursor(_meta(status=ReplayStatus.RUNNING, last_event_id=None), "10-1")


@pytest.mark.asyncio
async def test_closed_request_without_event_log_is_expired() -> None:
    store = ReplayStore()
    store._control_client = _ExistsClient(False)  # type: ignore[assignment]

    with pytest.raises(AppError) as exc_info:
        await store.ensure_replay_available(_meta(status=ReplayStatus.COMPLETED, last_event_id="10-3"))

    assert exc_info.value.code == int(ErrorCode.STREAM_REPLAY_EXPIRED)


@pytest.mark.asyncio
async def test_closed_request_with_event_log_remains_replayable() -> None:
    store = ReplayStore()
    store._control_client = _ExistsClient(True)  # type: ignore[assignment]

    await store.ensure_replay_available(_meta(status=ReplayStatus.COMPLETED, last_event_id="10-3"))


@pytest.mark.asyncio
async def test_blocking_reads_and_control_writes_use_different_clients() -> None:
    store = ReplayStore()
    control = _RoutingControlClient()
    subscriber = _RoutingSubscriberClient()
    store._control_client = control  # type: ignore[assignment]
    store._subscriber_client = subscriber  # type: ignore[assignment]

    await store.read_after("request-1", "0-0", block_ms=250, count=10)
    await store.signal_cancel(session_id=7, turn_id="turn-1")

    assert subscriber.xread_calls == [({"agent:sse:request:request-1:events": "0-0"}, 10, 250)]
    assert control.set_calls == [
        ("canvas:turn_cancel:7:turn-1", "1", settings.CANVAS_TURN_LOCK_TTL_SEC),
    ]


@pytest.mark.asyncio
async def test_publisher_flushes_answer_tokens_before_business_frame() -> None:
    store = _FakeStore()
    publisher = ReplayFramePublisher(store, "request-1")  # type: ignore[arg-type]

    await publisher.publish(
        create_stream_frame(type=StreamFrameType.TOKEN, channel=TokenChannel.ANSWER, text="今天")
    )
    await publisher.publish(
        create_stream_frame(type=StreamFrameType.TOKEN, channel=TokenChannel.ANSWER, text="好热")
    )
    await publisher.publish(create_stream_frame(type=StreamFrameType.DONE, turn_id="turn-1"))

    assert [frame.type for frame in store.frames] == [
        StreamFrameType.TOKEN,
        StreamFrameType.DONE,
    ]
    assert store.frames[0].text == "今天好热"  # type: ignore[union-attr]
    assert store.close_statuses == [None, ReplayStatus.COMPLETED]
    assert publisher.terminal_status == ReplayStatus.COMPLETED


@pytest.mark.asyncio
async def test_error_followed_by_done_keeps_failed_status() -> None:
    store = _FakeStore()
    publisher = ReplayFramePublisher(store, "request-1")  # type: ignore[arg-type]

    await publisher.publish(
        create_stream_frame(type=StreamFrameType.ERROR, code=StreamErrorCode.INTERNAL, message="failed")
    )
    await publisher.publish(create_stream_frame(type=StreamFrameType.DONE, turn_id="turn-1"))

    assert store.close_statuses == [ReplayStatus.FAILED]
    assert publisher.terminal_status == ReplayStatus.FAILED


@pytest.mark.asyncio
async def test_stream_replay_starts_strictly_after_cursor_and_closes() -> None:
    meta = _meta(status=ReplayStatus.COMPLETED, last_event_id="10-3")
    store = _FakeStore(meta)

    chunks = [chunk async for chunk in stream_replay(store, meta, last_event_id="10-1")]  # type: ignore[arg-type]

    assert chunks[0].startswith("id: 10-2\n")
    assert chunks[1].startswith("id: 10-3\n")
    assert len(chunks) == 2
    assert store.active_subscribers == 0


@pytest.mark.asyncio
async def test_stream_replay_recovers_after_subscriber_read_timeout() -> None:
    meta = _meta(status=ReplayStatus.COMPLETED, last_event_id="10-3")
    store = _TimeoutThenFramesStore(meta)

    chunks = [chunk async for chunk in stream_replay(store, meta, last_event_id=None)]  # type: ignore[arg-type]

    assert decode_encoded_sse_frame(chunks[0]).type == StreamFrameType.HEARTBEAT
    assert chunks[1].startswith("id: 10-2\n")
    assert chunks[2].startswith("id: 10-3\n")
    assert store.active_subscribers == 0


def test_decode_encoded_frame_uses_stream_contract() -> None:
    frame = decode_encoded_sse_frame(
        'data: {"type":"token","protocol_version":2,"channel":"answer","text":"完整帧"}\n\n'
    )
    assert frame.type == StreamFrameType.TOKEN
    assert frame.text == "完整帧"  # type: ignore[union-attr]
