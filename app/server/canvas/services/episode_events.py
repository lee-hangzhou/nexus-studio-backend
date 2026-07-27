from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.contracts.canvas import CanvasPatchResponse, GenerationProgress
from app.server.canvas.domain.constants import CANVAS_EPISODE_EVENTS_CHANNEL_TEMPLATE
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
    """订阅 Redis 集级通道直到 stop 置位"""
    channel = episode_events_channel(episode_id)
    pubsub = redis_client.pubsub()
    await pubsub.subscribe(channel)
    try:
        while not stop.is_set():
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
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
            await pubsub.unsubscribe(channel)
        finally:
            await pubsub.aclose()
