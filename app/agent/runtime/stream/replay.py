from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections.abc import AsyncIterator, Awaitable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel
from redis.asyncio import BlockingConnectionPool, ConnectionPool, Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

from app.agent.runtime.stream.encoder import encode_sse_frame
from app.agent.runtime.stream.frames import StreamFrame, StreamFrameType, create_stream_frame
from app.contracts.stream import STREAM_FRAME_ADAPTER, TokenFrame
from app.server.chat.domain.stream_enums import StreamErrorCode, TokenChannel
from app.server.exceptions.base import AppError
from app.server.exceptions.codes import ErrorCode
from app.server.infra.config import settings
from app.server.infra.logger import logger

_EVENT_ID_RE = re.compile(r"^(0|[1-9][0-9]*)-(0|[1-9][0-9]*)$")


class ReplayRequestKind(StrEnum):
    TURN = "turn"
    RESUME = "resume"


class ReplayStatus(StrEnum):
    STARTING = "starting"
    RUNNING = "running"
    INTERRUPTED = "interrupted"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


_CLOSED_STATUSES = frozenset({
    ReplayStatus.INTERRUPTED,
    ReplayStatus.COMPLETED,
    ReplayStatus.FAILED,
    ReplayStatus.CANCELLED,
})


class ReplayLogLimitExceeded(RuntimeError):
    pass


class ReplayExecutionClosed(RuntimeError):
    pass


@dataclass(frozen=True)
class ReplayMeta:
    request_id: str
    user_id: int
    session_id: int
    turn_id: str
    kind: ReplayRequestKind
    fingerprint: str
    status: ReplayStatus
    last_event_id: str | None
    bytes_written: int
    created_at: str
    finished_at: str | None

    @property
    def closed(self) -> bool:
        return self.status in _CLOSED_STATUSES


@dataclass(frozen=True)
class ReplayClaim:
    meta: ReplayMeta
    created: bool


def build_request_fingerprint(
    *,
    kind: ReplayRequestKind,
    user_id: int,
    session_id: int,
    body: BaseModel | Mapping[str, Any],
) -> str:
    if isinstance(body, BaseModel):
        payload = body.model_dump(mode="json", exclude={"request_id"}, exclude_none=False)
    else:
        payload = {key: value for key, value in body.items() if key != "request_id"}
    envelope = {
        "kind": kind.value,
        "user_id": user_id,
        "session_id": session_id,
        "body": payload,
    }
    canonical = json.dumps(envelope, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def validate_event_id(value: str | None) -> str:
    resolved = (value or "").strip()
    if not resolved:
        return "0-0"
    if _EVENT_ID_RE.fullmatch(resolved) is None:
        raise AppError(ErrorCode.INVALID_PARAMS, "invalid Last-Event-ID")
    return resolved


def _event_id_tuple(value: str) -> tuple[int, int]:
    major, minor = value.split("-", 1)
    return int(major), int(minor)


def validate_replay_cursor(meta: ReplayMeta, value: str | None) -> str:
    cursor = validate_event_id(value)
    if cursor == "0-0":
        return cursor
    if meta.last_event_id is None or _event_id_tuple(cursor) > _event_id_tuple(meta.last_event_id):
        raise AppError(ErrorCode.INVALID_PARAMS, "Last-Event-ID is ahead of the stream")
    return cursor


class ReplayStore:
    _CLAIM_SCRIPT = """
local key = KEYS[1]
if redis.call('EXISTS', key) == 1 then
  return 0
end
redis.call('DEL', KEYS[2], KEYS[3])
redis.call('HSET', key,
  'request_id', ARGV[1], 'user_id', ARGV[2], 'session_id', ARGV[3],
  'turn_id', ARGV[4], 'kind', ARGV[5], 'fingerprint', ARGV[6],
  'status', ARGV[7], 'bytes_written', '0', 'created_at', ARGV[8])
redis.call('EXPIRE', key, ARGV[9])
return 1
"""

    _SET_RUNNING_SCRIPT = """
local meta = KEYS[1]
local lease = KEYS[2]
if redis.call('HGET', meta, 'status') ~= 'starting' then
  return 0
end
if redis.call('SET', lease, ARGV[1], 'EX', ARGV[2], 'NX') == false then
  return 0
end
redis.call('HSET', meta, 'status', 'running')
return 1
"""

    _APPEND_SCRIPT = """
local meta = KEYS[1]
local events = KEYS[2]
if redis.call('EXISTS', meta) == 0 then
  return redis.error_reply('replay metadata missing')
end
local status = redis.call('HGET', meta, 'status')
if status ~= 'starting' and status ~= 'running' then
  return {'CLOSED'}
end
local current = tonumber(redis.call('HGET', meta, 'bytes_written') or '0')
local added = tonumber(ARGV[2])
local maximum = tonumber(ARGV[3])
local force = ARGV[4] == '1'
if not force and current + added > maximum then
  return {'LIMIT'}
end
local id = redis.call('XADD', events, '*', 'frame', ARGV[1])
redis.call('HSET', meta, 'last_event_id', id, 'bytes_written', current + added)
redis.call('EXPIRE', meta, ARGV[7])
redis.call('EXPIRE', events, ARGV[8])
if ARGV[5] ~= '' then
  redis.call('HSET', meta, 'status', ARGV[5], 'finished_at', ARGV[6])
  redis.call('DEL', KEYS[3])
end
return {'OK', id}
"""

    _FINISH_SCRIPT = """
local current = redis.call('HGET', KEYS[1], 'status')
if current == 'starting' or current == 'running' then
  redis.call('HSET', KEYS[1], 'status', ARGV[1], 'finished_at', ARGV[2])
end
redis.call('EXPIRE', KEYS[1], ARGV[3])
redis.call('EXPIRE', KEYS[2], ARGV[4])
redis.call('DEL', KEYS[3])
return current
"""

    _REFRESH_LEASE_SCRIPT = """
if redis.call('GET', KEYS[1]) ~= ARGV[1] then
  return 0
end
redis.call('EXPIRE', KEYS[1], ARGV[2])
return 1
"""

    _MARK_LOST_SCRIPT = """
local meta = KEYS[1]
local events = KEYS[2]
local lease = KEYS[3]
local status = redis.call('HGET', meta, 'status')
if status ~= 'starting' and status ~= 'running' then
  return 0
end
if redis.call('EXISTS', lease) == 1 then
  return 0
end
local current = tonumber(redis.call('HGET', meta, 'bytes_written') or '0')
local id = redis.call('XADD', events, '*', 'frame', ARGV[1])
redis.call('HSET', meta,
  'last_event_id', id, 'bytes_written', current + tonumber(ARGV[2]),
  'status', 'failed', 'finished_at', ARGV[3])
redis.call('EXPIRE', meta, ARGV[4])
redis.call('EXPIRE', events, ARGV[5])
if #KEYS >= 4 and redis.call('GET', KEYS[4]) == ARGV[6] then
  redis.call('DEL', KEYS[4])
end
return 1
"""

    def __init__(self) -> None:
        self._control_pool: ConnectionPool | None = None
        self._control_client: Redis | None = None
        self._subscriber_pool: BlockingConnectionPool | None = None
        self._subscriber_client: Redis | None = None
        self._subscriber_count_lock = asyncio.Lock()
        self._active_subscribers = 0

    async def connect(self) -> None:
        if self._control_client is not None and self._subscriber_client is not None:
            return
        if self._control_client is not None or self._subscriber_client is not None:
            raise RuntimeError("replay store is only partially connected")

        self._control_pool = ConnectionPool.from_url(
            settings.REDIS_URL,
            max_connections=settings.SSE_REDIS_CONTROL_MAX_CONNECTIONS,
            decode_responses=True,
        )
        self._subscriber_pool = BlockingConnectionPool.from_url(
            settings.REDIS_URL,
            max_connections=settings.SSE_REDIS_SUBSCRIBER_MAX_CONNECTIONS,
            timeout=settings.SSE_REDIS_SUBSCRIBER_ACQUIRE_TIMEOUT_SEC,
            # XREAD blocks for one heartbeat window. redis-py 8 defaults to a
            # 5s socket timeout, which would abort healthy idle subscriptions.
            socket_timeout=settings.SSE_REDIS_SUBSCRIBER_SOCKET_TIMEOUT_SEC,
            decode_responses=True,
        )
        self._control_client = Redis(connection_pool=self._control_pool)
        self._subscriber_client = Redis(connection_pool=self._subscriber_pool)
        try:
            await asyncio.gather(self._control_client.ping(), self._subscriber_client.ping())
        except Exception:
            await self.disconnect()
            raise

    async def disconnect(self) -> None:
        clients = [client for client in (self._control_client, self._subscriber_client) if client is not None]
        pools = [pool for pool in (self._control_pool, self._subscriber_pool) if pool is not None]
        self._control_client = None
        self._subscriber_client = None
        self._control_pool = None
        self._subscriber_pool = None
        try:
            if clients:
                await asyncio.gather(*(client.aclose() for client in clients))
        finally:
            if pools:
                await asyncio.gather(*(pool.disconnect() for pool in pools))

    @property
    def control_client(self) -> Redis:
        if self._control_client is None:
            raise RuntimeError("replay store control client is not connected")
        return self._control_client

    @property
    def subscriber_client(self) -> Redis:
        if self._subscriber_client is None:
            raise RuntimeError("replay store subscriber client is not connected")
        return self._subscriber_client

    async def register_subscriber(self) -> int:
        async with self._subscriber_count_lock:
            self._active_subscribers += 1
            return self._active_subscribers

    async def unregister_subscriber(self) -> int:
        async with self._subscriber_count_lock:
            self._active_subscribers = max(0, self._active_subscribers - 1)
            return self._active_subscribers

    @staticmethod
    def _meta_key(request_id: str) -> str:
        return f"agent:sse:request:{request_id}:meta"

    @staticmethod
    def _events_key(request_id: str) -> str:
        return f"agent:sse:request:{request_id}:events"

    @staticmethod
    def _lease_key(request_id: str) -> str:
        return f"agent:sse:request:{request_id}:lease"

    @staticmethod
    def _cancel_key(session_id: int, turn_id: str) -> str:
        return f"canvas:turn_cancel:{session_id}:{turn_id}"

    async def claim(
        self,
        *,
        request_id: str,
        user_id: int,
        session_id: int,
        turn_id: str,
        kind: ReplayRequestKind,
        fingerprint: str,
    ) -> ReplayClaim:
        created_at = datetime.now(UTC).isoformat()
        created = bool(
            await self.control_client.eval(
                self._CLAIM_SCRIPT,
                3,
                self._meta_key(request_id),
                self._events_key(request_id),
                self._lease_key(request_id),
                request_id,
                str(user_id),
                str(session_id),
                turn_id,
                kind.value,
                fingerprint,
                ReplayStatus.STARTING.value,
                created_at,
                str(settings.SSE_REPLAY_META_TTL_SEC),
            )
        )
        meta = await self.require_meta(request_id)
        if not created and (
            meta.user_id != user_id
            or meta.session_id != session_id
            or meta.kind != kind
            or meta.fingerprint != fingerprint
        ):
            raise AppError(ErrorCode.STREAM_REQUEST_CONFLICT, "requestId is already used by another request")
        if not created:
            await self.ensure_replay_available(meta)
        return ReplayClaim(meta=meta, created=created)

    async def require_owned_meta(self, request_id: str, *, user_id: int, session_id: int) -> ReplayMeta:
        meta = await self.get_meta(request_id)
        if meta is None or meta.user_id != user_id or meta.session_id != session_id:
            raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "stream request not found")
        await self.ensure_replay_available(meta)
        return meta

    async def ensure_replay_available(self, meta: ReplayMeta) -> None:
        if meta.last_event_id is None or not meta.closed:
            return
        if not await self.control_client.exists(self._events_key(meta.request_id)):
            raise AppError(ErrorCode.STREAM_REPLAY_EXPIRED, "stream replay has expired")

    async def require_meta(self, request_id: str) -> ReplayMeta:
        meta = await self.get_meta(request_id)
        if meta is None:
            raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "stream request not found")
        return meta

    async def get_meta(self, request_id: str) -> ReplayMeta | None:
        raw = await self.control_client.hgetall(self._meta_key(request_id))
        if not raw:
            return None
        return ReplayMeta(
            request_id=raw["request_id"],
            user_id=int(raw["user_id"]),
            session_id=int(raw["session_id"]),
            turn_id=raw["turn_id"],
            kind=ReplayRequestKind(raw["kind"]),
            fingerprint=raw["fingerprint"],
            status=ReplayStatus(raw["status"]),
            last_event_id=raw.get("last_event_id") or None,
            bytes_written=int(raw.get("bytes_written", "0")),
            created_at=raw["created_at"],
            finished_at=raw.get("finished_at") or None,
        )

    async def set_running(self, request_id: str, *, lease_owner: str) -> None:
        ok = await self.control_client.eval(
            self._SET_RUNNING_SCRIPT,
            2,
            self._meta_key(request_id),
            self._lease_key(request_id),
            lease_owner,
            str(settings.SSE_EXECUTION_LEASE_TTL_SEC),
        )
        if not ok:
            raise RuntimeError("stream execution cannot transition from starting to running")

    async def refresh_lease(self, request_id: str, *, lease_owner: str) -> bool:
        result = await self.control_client.eval(
            self._REFRESH_LEASE_SCRIPT,
            1,
            self._lease_key(request_id),
            lease_owner,
            str(settings.SSE_EXECUTION_LEASE_TTL_SEC),
        )
        return bool(result)

    async def has_lease(self, request_id: str) -> bool:
        return bool(await self.control_client.exists(self._lease_key(request_id)))

    async def mark_execution_lost(
        self,
        request_id: str,
        *,
        execution_lock_key: str | None = None,
        execution_lock_owner: str | None = None,
    ) -> bool:
        frame = create_stream_frame(
            type=StreamFrameType.ERROR,
            code=StreamErrorCode.EXECUTION_LOST,
            message="Agent execution was lost",
        )
        payload = json.dumps(
            frame.model_dump(mode="json", by_alias=True, exclude_none=True),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        keys = [
            self._meta_key(request_id),
            self._events_key(request_id),
            self._lease_key(request_id),
        ]
        if execution_lock_key is not None:
            keys.append(execution_lock_key)
        result = await self.control_client.eval(
            self._MARK_LOST_SCRIPT,
            len(keys),
            *keys,
            payload,
            str(len(payload.encode("utf-8"))),
            datetime.now(UTC).isoformat(),
            str(settings.SSE_REPLAY_META_TTL_SEC),
            str(settings.SSE_REPLAY_TTL_SEC),
            execution_lock_owner or "",
        )
        return bool(result)

    async def append_frame(
        self,
        request_id: str,
        frame: StreamFrame,
        *,
        force: bool = False,
        close_status: ReplayStatus | None = None,
    ) -> str:
        payload = json.dumps(
            frame.model_dump(mode="json", by_alias=True, exclude_none=True),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        result = await self.control_client.eval(
            self._APPEND_SCRIPT,
            3,
            self._meta_key(request_id),
            self._events_key(request_id),
            self._lease_key(request_id),
            payload,
            str(len(payload.encode("utf-8"))),
            str(settings.SSE_REPLAY_MAX_BYTES),
            "1" if force else "0",
            close_status.value if close_status is not None else "",
            datetime.now(UTC).isoformat(),
            str(settings.SSE_REPLAY_META_TTL_SEC),
            str(settings.SSE_REPLAY_TTL_SEC),
        )
        if not result or result[0] == "LIMIT":
            raise ReplayLogLimitExceeded("stream replay log size exceeded")
        if result[0] == "CLOSED":
            raise ReplayExecutionClosed("stream execution is already closed")
        return str(result[1])

    async def finish(self, request_id: str, status: ReplayStatus) -> None:
        now = datetime.now(UTC).isoformat()
        await self.control_client.eval(
            self._FINISH_SCRIPT,
            3,
            self._meta_key(request_id),
            self._events_key(request_id),
            self._lease_key(request_id),
            status.value,
            now,
            str(settings.SSE_REPLAY_META_TTL_SEC),
            str(settings.SSE_REPLAY_TTL_SEC),
        )

    async def discard_starting(self, request_id: str) -> None:
        """启动前失败时删除尚未产生事件的 claim。"""
        meta = await self.get_meta(request_id)
        if meta is None or meta.status != ReplayStatus.STARTING or meta.last_event_id is not None:
            return
        await self.control_client.delete(
            self._meta_key(request_id),
            self._events_key(request_id),
            self._lease_key(request_id),
        )

    async def read_after(
        self,
        request_id: str,
        event_id: str,
        *,
        block_ms: int,
        count: int = 100,
    ) -> list[tuple[str, str]]:
        result = await self.subscriber_client.xread(
            {self._events_key(request_id): event_id},
            count=count,
            block=block_ms,
        )
        if not result:
            return []
        entries: list[tuple[str, str]] = []
        for _, rows in result:
            for row_id, fields in rows:
                frame = fields.get("frame")
                if frame is not None:
                    entries.append((str(row_id), str(frame)))
        return entries

    async def signal_cancel(self, *, session_id: int, turn_id: str) -> None:
        await self.control_client.set(
            self._cancel_key(session_id, turn_id),
            "1",
            ex=settings.CANVAS_TURN_LOCK_TTL_SEC,
        )

    async def is_cancel_requested(self, *, session_id: int, turn_id: str) -> bool:
        return bool(await self.control_client.exists(self._cancel_key(session_id, turn_id)))

    async def clear_cancel(self, *, session_id: int, turn_id: str) -> None:
        await self.control_client.delete(self._cancel_key(session_id, turn_id))


class ReplayFramePublisher:
    def __init__(self, store: ReplayStore, request_id: str) -> None:
        self._store = store
        self._request_id = request_id
        self._lock = asyncio.Lock()
        self._delta_parts: list[str] = []
        self._delta_chars = 0
        self._delta_protocol_version = settings.CHAT_SSE_PROTOCOL_VERSION
        self._flush_task: asyncio.Task[None] | None = None
        self._flush_error: Exception | None = None
        self.terminal_status: ReplayStatus | None = None
        self.saw_pending = False

    async def publish(self, frame: StreamFrame) -> None:
        self._raise_flush_error()
        if self.terminal_status is not None:
            return
        if isinstance(frame, TokenFrame) and frame.channel == TokenChannel.ANSWER:
            async with self._lock:
                self._delta_parts.append(frame.text)
                self._delta_chars += len(frame.text)
                self._delta_protocol_version = frame.protocol_version
                if self._delta_chars >= settings.SSE_DELTA_MAX_CHARS:
                    await self._flush_locked()
                elif self._flush_task is None:
                    self._flush_task = asyncio.create_task(self._flush_after_delay())
            return
        async with self._lock:
            await self._flush_locked()
            close_status = self._terminal_status_for(frame)
            await self._store.append_frame(self._request_id, frame, close_status=close_status)
            self._observe(frame)

    async def close(self) -> None:
        async with self._lock:
            await self._flush_locked()
        self._raise_flush_error()

    async def append_failure(self, message: str, *, code: StreamErrorCode = StreamErrorCode.INTERNAL) -> None:
        async with self._lock:
            try:
                await self._flush_locked()
            except ReplayLogLimitExceeded:
                self._delta_parts.clear()
                self._delta_chars = 0
            frame = create_stream_frame(type=StreamFrameType.ERROR, code=code, message=message)
            await self._store.append_frame(
                self._request_id,
                frame,
                force=True,
                close_status=ReplayStatus.FAILED,
            )
            self.terminal_status = ReplayStatus.FAILED

    async def _flush_after_delay(self) -> None:
        try:
            await asyncio.sleep(settings.SSE_DELTA_FLUSH_INTERVAL_MS / 1000)
            async with self._lock:
                self._flush_task = None
                await self._flush_locked()
        except asyncio.CancelledError:
            return
        except Exception as exc:
            self._flush_error = exc

    async def _flush_locked(self) -> None:
        current = asyncio.current_task()
        if self._flush_task is not None and self._flush_task is not current:
            self._flush_task.cancel()
        self._flush_task = None
        if not self._delta_parts:
            return
        text = "".join(self._delta_parts)
        self._delta_parts.clear()
        self._delta_chars = 0
        await self._store.append_frame(
            self._request_id,
            create_stream_frame(
                type=StreamFrameType.TOKEN,
                channel=TokenChannel.ANSWER,
                protocol_version=self._delta_protocol_version,
                text=text,
            ),
        )

    def _raise_flush_error(self) -> None:
        if self._flush_error is None:
            return
        error = self._flush_error
        self._flush_error = None
        raise error

    def _observe(self, frame: StreamFrame) -> None:
        if frame.type in (
            StreamFrameType.TOOL_PENDING,
            StreamFrameType.USER_GATE_REQUIRED,
            StreamFrameType.UPGRADE_INVITE_PROPOSED,
        ):
            self.saw_pending = True
        elif frame.type == StreamFrameType.DONE and self.terminal_status is None:
            self.terminal_status = ReplayStatus.COMPLETED
        elif frame.type == StreamFrameType.ERROR:
            self.terminal_status = ReplayStatus.FAILED
        elif frame.type == StreamFrameType.CANCELLED and self.terminal_status is None:
            self.terminal_status = ReplayStatus.CANCELLED

    def _terminal_status_for(self, frame: StreamFrame) -> ReplayStatus | None:
        if frame.type == StreamFrameType.ERROR:
            return ReplayStatus.FAILED
        if frame.type == StreamFrameType.CANCELLED:
            return self.terminal_status or ReplayStatus.CANCELLED
        if frame.type == StreamFrameType.DONE:
            return self.terminal_status or ReplayStatus.COMPLETED
        return None


def decode_encoded_sse_frame(chunk: str) -> StreamFrame:
    data_parts: list[str] = []
    for line in chunk.splitlines():
        if not line.startswith("data:"):
            continue
        value = line[5:]
        if value.startswith(" "):
            value = value[1:]
        data_parts.append(value)
    if not data_parts:
        raise ValueError("encoded SSE chunk has no data field")
    payload = json.loads("\n".join(data_parts))
    return STREAM_FRAME_ADAPTER.validate_python(payload)


async def stream_replay(
    store: ReplayStore,
    meta: ReplayMeta,
    *,
    last_event_id: str | None,
    execution_lock_key: str | None = None,
    execution_lock_owner: str | None = None,
) -> AsyncIterator[str]:
    cursor = validate_replay_cursor(meta, last_event_id)
    replayed_count = 0
    active_subscribers = await store.register_subscriber()
    try:
        logger.info(
            "turn.replay.subscribe",
            request_id=meta.request_id,
            turn_id=meta.turn_id,
            cursor=cursor,
            status=meta.status.value,
            active_subscribers=active_subscribers,
        )
        if active_subscribers >= settings.SSE_ACTIVE_SUBSCRIBER_WARN_THRESHOLD:
            logger.warning(
                "turn.replay.subscriber_capacity_warning",
                active_subscribers=active_subscribers,
                warning_threshold=settings.SSE_ACTIVE_SUBSCRIBER_WARN_THRESHOLD,
                pool_max_connections=settings.SSE_REDIS_SUBSCRIBER_MAX_CONNECTIONS,
            )

        while True:
            try:
                entries = await store.read_after(
                    meta.request_id,
                    cursor,
                    block_ms=settings.sse_replay_heartbeat_interval_sec * 1000,
                )
            except RedisTimeoutError as exc:
                logger.warning(
                    "turn.replay.subscriber_read_timeout",
                    request_id=meta.request_id,
                    turn_id=meta.turn_id,
                    active_subscribers=active_subscribers,
                    error=str(exc),
                )
                entries = []
            except RedisConnectionError as exc:
                logger.warning(
                    "turn.replay.subscriber_read_failed",
                    request_id=meta.request_id,
                    turn_id=meta.turn_id,
                    active_subscribers=active_subscribers,
                    error=str(exc),
                )
                raise
            if entries:
                for event_id, frame_json in entries:
                    cursor = event_id
                    replayed_count += 1
                    yield f"id: {event_id}\ndata: {frame_json}\n\n"
                current = await store.require_meta(meta.request_id)
                if current.closed and cursor == current.last_event_id:
                    return
                continue

            current = await store.require_meta(meta.request_id)
            if current.closed and (current.last_event_id is None or cursor == current.last_event_id):
                return
            active_without_lease = (
                current.status in (ReplayStatus.STARTING, ReplayStatus.RUNNING)
                and not await store.has_lease(meta.request_id)
            )
            if active_without_lease:
                created_at = datetime.fromisoformat(current.created_at)
                age_sec = (datetime.now(UTC) - created_at).total_seconds()
                if current.status == ReplayStatus.RUNNING or age_sec >= settings.SSE_EXECUTION_LEASE_TTL_SEC:
                    await store.mark_execution_lost(
                        meta.request_id,
                        execution_lock_key=execution_lock_key,
                        execution_lock_owner=execution_lock_owner,
                    )
                    continue
            yield encode_sse_frame(
                create_stream_frame(
                    type=StreamFrameType.HEARTBEAT,
                    turn_id=meta.turn_id,
                    ts=int(datetime.now(UTC).timestamp()),
                )
            )
    finally:
        active_subscribers = await store.unregister_subscriber()
        logger.info(
            "turn.replay.unsubscribe",
            request_id=meta.request_id,
            turn_id=meta.turn_id,
            cursor=cursor,
            delivered_count=replayed_count,
            active_subscribers=active_subscribers,
        )


replay_store = ReplayStore()


def new_lease_owner() -> str:
    return uuid4().hex
