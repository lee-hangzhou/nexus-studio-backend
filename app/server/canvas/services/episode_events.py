"""集级画布事件: Redis Pub/Sub, 非进程内 hub。

PG 是图权威; Redis 只做跨 worker / 跨页签加速通知。

订阅连接必须用独立 Blocking 池, 禁止占用 redis_client 共享短连接池。
Pub/Sub 在 redis-py 中会独占一条 TCP 连接直到 aclose。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from redis.asyncio import BlockingConnectionPool, Redis

from app.contracts.canvas import CanvasPatchResponse, GenerationProgress
from app.server.canvas.domain.constants import CANVAS_EPISODE_EVENTS_CHANNEL_TEMPLATE
from app.server.infra.config import settings
from app.server.infra.logger import logger
from app.server.infra.redis import redis_client


class CanvasSessionTitleEvent(BaseModel):
    """画布会话标题变更"""

    model_config = ConfigDict(extra="forbid")

    session_id: int = Field(ge=1)
    title: str = Field(min_length=1)
    updated_at: str = ""


class CanvasEpisodeEvent(BaseModel):
    """集级 Redis/SSE 事件载荷"""

    model_config = ConfigDict(extra="forbid")

    canvas_patch: CanvasPatchResponse | None = None
    generation_progress: GenerationProgress | None = None
    session_title: CanvasSessionTitleEvent | None = None


class CanvasEpisodeEventsBus:
    """集级 Pub/Sub 订阅侧连接池 (与短命令共享池隔离)"""

    def __init__(self) -> None:
        self._pool: BlockingConnectionPool | None = None
        self._client: Redis | None = None
        self._subscriber_count_lock = asyncio.Lock()
        self._active_subscribers = 0

    async def connect(self) -> None:
        """启动独立订阅池"""
        if self._client is not None:
            return
        self._pool = BlockingConnectionPool.from_url(
            settings.REDIS_URL,
            max_connections=settings.CANVAS_EPISODE_EVENTS_REDIS_MAX_CONNECTIONS,
            timeout=settings.CANVAS_EPISODE_EVENTS_REDIS_ACQUIRE_TIMEOUT_SEC,
            # get_message(timeout=1) 空闲轮询; 避免默认 5s socket 误杀健康订阅
            socket_timeout=settings.CANVAS_EPISODE_EVENTS_REDIS_SOCKET_TIMEOUT_SEC,
            decode_responses=True,
        )
        self._client = Redis(connection_pool=self._pool)
        try:
            await self._client.ping()
        except Exception:
            await self.disconnect()
            raise

    async def disconnect(self) -> None:
        """关闭订阅池"""
        client = self._client
        pool = self._pool
        self._client = None
        self._pool = None
        try:
            if client is not None:
                await client.aclose()
        finally:
            if pool is not None:
                await pool.disconnect()

    def _ensure_client(self) -> Redis:
        """已 connect 的订阅客户端"""
        if self._client is None:
            raise RuntimeError("canvas episode events bus is not connected")
        return self._client

    async def register_subscriber(self) -> int:
        """登记活跃订阅数"""
        async with self._subscriber_count_lock:
            self._active_subscribers += 1
            return self._active_subscribers

    async def unregister_subscriber(self) -> int:
        """注销活跃订阅数"""
        async with self._subscriber_count_lock:
            self._active_subscribers = max(0, self._active_subscribers - 1)
            return self._active_subscribers

    def pubsub(self) -> object:
        """新建 PubSub (独占池内一条连接至 aclose)"""
        return self._ensure_client().pubsub()


canvas_episode_events_bus = CanvasEpisodeEventsBus()


def episode_events_channel(episode_id: int) -> str:
    """拼集级 Redis channel 名"""
    return CANVAS_EPISODE_EVENTS_CHANNEL_TEMPLATE.format(episode_id=episode_id)


def _event_has_payload(event: CanvasEpisodeEvent) -> bool:
    """事件是否含可下发载荷"""
    return (
        event.canvas_patch is not None
        or event.generation_progress is not None
        or event.session_title is not None
    )


async def publish_episode_event(episode_id: int, event: CanvasEpisodeEvent) -> None:
    """发布集级事件; Redis 失败只记日志不回滚 PG"""
    if not _event_has_payload(event):
        return
    channel = episode_events_channel(episode_id)
    try:
        await redis_client.publish(
            channel,
            event.model_dump_json(exclude_none=True),
        )
    except Exception as exc:
        logger.error(
            "canvas.episode_events.publish_failed",
            episode_id=episode_id,
            error=str(exc),
        )


async def publish_canvas_patch(episode_id: int, patch: CanvasPatchResponse) -> None:
    """发布图增量补丁"""
    await publish_episode_event(episode_id, CanvasEpisodeEvent(canvas_patch=patch))


async def publish_generation_progress(episode_id: int, progress: GenerationProgress) -> None:
    """发布节点生成进度"""
    await publish_episode_event(episode_id, CanvasEpisodeEvent(generation_progress=progress))


async def publish_patch_and_progress(
    episode_id: int,
    *,
    canvas_patch: CanvasPatchResponse | None = None,
    progress: GenerationProgress | None = None,
) -> None:
    """同时发布补丁与进度"""
    await publish_episode_event(
        episode_id,
        CanvasEpisodeEvent(canvas_patch=canvas_patch, generation_progress=progress),
    )


async def publish_session_title(
    episode_id: int,
    *,
    session_id: int,
    title: str,
    updated_at: str,
) -> None:
    """发布会话标题变更"""
    await publish_episode_event(
        episode_id,
        CanvasEpisodeEvent(
            session_title=CanvasSessionTitleEvent(
                session_id=session_id,
                title=title,
                updated_at=updated_at,
            )
        ),
    )


async def iter_episode_events(
    episode_id: int,
    *,
    stop: asyncio.Event,
) -> AsyncIterator[CanvasEpisodeEvent]:
    """订阅 Redis 集级通道直到 stop 置位; 占用独立订阅池连接"""
    channel = episode_events_channel(episode_id)
    active = await canvas_episode_events_bus.register_subscriber()
    logger.info(
        "canvas.episode_events.subscribe",
        episode_id=episode_id,
        active_subscribers=active,
        pool_max_connections=settings.CANVAS_EPISODE_EVENTS_REDIS_MAX_CONNECTIONS,
    )
    if active >= settings.CANVAS_EPISODE_EVENTS_ACTIVE_WARN_THRESHOLD:
        logger.warning(
            "canvas.episode_events.subscriber_capacity_warning",
            episode_id=episode_id,
            active_subscribers=active,
            pool_max_connections=settings.CANVAS_EPISODE_EVENTS_REDIS_MAX_CONNECTIONS,
            warn_threshold=settings.CANVAS_EPISODE_EVENTS_ACTIVE_WARN_THRESHOLD,
        )

    pubsub = canvas_episode_events_bus.pubsub()
    try:
        await pubsub.subscribe(channel)  # type: ignore[misc]
        while not stop.is_set():
            message = await pubsub.get_message(  # type: ignore[misc]
                ignore_subscribe_messages=True,
                timeout=1.0,
            )
            if message is None:
                await asyncio.sleep(0)
                continue
            if message.get("type") != "message":
                continue
            raw = message.get("data")
            if not isinstance(raw, str):
                continue
            try:
                yield CanvasEpisodeEvent.model_validate_json(raw)
            except (ValidationError, json.JSONDecodeError):
                logger.warning("canvas.episode_events.bad_payload", episode_id=episode_id)
                continue
    finally:
        try:
            await pubsub.unsubscribe(channel)  # type: ignore[misc]
        except Exception as exc:
            logger.warning(
                "canvas.episode_events.unsubscribe_failed",
                episode_id=episode_id,
                error=str(exc),
            )
        try:
            await pubsub.aclose()  # type: ignore[misc]
        except Exception as exc:
            logger.warning(
                "canvas.episode_events.pubsub_close_failed",
                episode_id=episode_id,
                error=str(exc),
            )
        remaining = await canvas_episode_events_bus.unregister_subscriber()
        logger.info(
            "canvas.episode_events.unsubscribe",
            episode_id=episode_id,
            active_subscribers=remaining,
        )
