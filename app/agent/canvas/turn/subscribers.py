"""Canvas turn side effects: persistence, patches, generation hub fanout."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from app.agent.canvas.turn.generation_hub import canvas_generation_hub
from app.agent.canvas.turn.memory_background import schedule_canvas_memory_extract
from app.agent.canvas.turn.persistence import (
    persist_canvas_assistant_message,
    persist_canvas_tool_step,
    persist_canvas_user_message,
)
from app.agent.chat.tools.ui_preview import sanitize_tool_step_preview
from app.agent.runtime.ports import get_canvas_port
from app.agent.runtime.stream.frames import StreamFrameType, create_stream_frame
from app.agent.runtime.tools.result import ToolResult, ToolResultProtocolError
from app.agent.runtime.turn_engine.events import (
    ToolFinished,
    TurnCompleted,
    TurnEvent,
    TurnEventKind,
    TurnFailed,
    TurnStarting,
)
from app.agent.runtime.turn_engine.subscribers import TurnEmit
from app.contracts.metadata import CanvasToolStepMetadata

_PATCH_TOOLS = frozenset({"apply_canvas_patch", "submit_node_generation"})


def _tool_output_payload(event: ToolFinished) -> dict[str, Any] | None:
    """Decode successful ToolResult.output JSON for canvas SSE side effects."""
    try:
        parsed = ToolResult.parse_tool_message(event.tool_result or "")
    except ToolResultProtocolError:
        return None
    if not parsed.success:
        return None
    try:
        data = json.loads(parsed.output or "{}")
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


async def _emit_canvas_patch_from_tool(event: ToolFinished, emit: TurnEmit) -> None:
    name = event.tool_name or ""
    if name not in _PATCH_TOOLS:
        return
    data = _tool_output_payload(event)
    if not data:
        return
    if name == "submit_node_generation":
        rev = data.get("revision")
        node_id = data.get("node_id")
        if rev is not None and node_id:
            await emit(
                create_stream_frame(
                    type=StreamFrameType.GENERATION_PROGRESS,
                    turn_id=None,
                    data={
                        "node_id": node_id,
                        "task_id": data.get("task_id"),
                        "status": "running",
                        "revision": rev,
                    },
                )
            )
        return
    delta = {
        "revision": data.get("revision"),
        "op_id": data.get("op_id"),
        "nodes": data.get("nodes", []),
        "edges": data.get("edges", []),
        "deleted_node_ids": data.get("deleted_node_ids", []),
        "deleted_edge_ids": data.get("deleted_edge_ids", []),
    }
    if delta.get("revision") is not None:
        await emit(create_stream_frame(type=StreamFrameType.CANVAS_PATCH, data=delta))


async def _touch_episode(episode_id: int) -> None:
    await get_canvas_port().touch_episode(episode_id)


class CanvasPersistenceSubscriber:
    """Persist user/assistant/tool steps. DONE/ERROR owned by SseTurnSubscriber."""

    barrier_events = frozenset(
        {
            TurnEventKind.TURN_STARTING,
            TurnEventKind.TURN_COMPLETED,
            TurnEventKind.TURN_FAILED,
        }
    )
    broadcast_events = frozenset({TurnEventKind.TOOL_FINISHED})

    def __init__(
        self,
        *,
        project_id: int,
        episode_id: int,
        user_id: int,
        content: str,
        client_turn_id: str | None,
        enable_tools: bool,
        persist_user: bool = True,
        schedule_memory: bool = True,
    ) -> None:
        self._project_id = project_id
        self._episode_id = episode_id
        self._user_id = user_id
        self._content = content
        self._client_turn_id = client_turn_id
        self._enable_tools = enable_tools
        self._persist_user = persist_user
        self._schedule_memory = schedule_memory

    async def handle(self, event: TurnEvent, *, emit: TurnEmit) -> None:
        if isinstance(event, TurnStarting) and self._persist_user:
            await persist_canvas_user_message(
                episode_id=self._episode_id,
                user_id=self._user_id,
                content=self._content,
                client_turn_id=self._client_turn_id,
                turn_id=event.turn_id,
            )
            return

        if isinstance(event, ToolFinished) and self._enable_tools:
            preview = sanitize_tool_step_preview(
                event.tool_name,
                event.tool_result,
                ok=not event.tool_error,
            )
            await persist_canvas_tool_step(
                episode_id=self._episode_id,
                user_id=self._user_id,
                turn_id=event.turn_id,
                step=CanvasToolStepMetadata(
                    call_id=event.call_id,
                    name=event.tool_name,
                    ok=not event.tool_error,
                    preview=preview,
                    error_type=event.error_class,
                ),
            )
            await _emit_canvas_patch_from_tool(event, emit)
            return

        if isinstance(event, TurnFailed):
            await _touch_episode(self._episode_id)
            return

        if isinstance(event, TurnCompleted):
            answer_text = (event.answer_text or "").strip()
            if not answer_text:
                # Empty answers fail in CanvasEmptyAnswerHook before TurnCompleted.
                return

            await persist_canvas_assistant_message(
                episode_id=self._episode_id,
                user_id=self._user_id,
                content=answer_text,
                client_turn_id=self._client_turn_id,
                turn_id=event.turn_id,
                tool_calls_count=event.tool_calls_count,
            )
            await _touch_episode(self._episode_id)
            if self._schedule_memory:
                schedule_canvas_memory_extract(
                    messages=list(event.messages),
                    user_id=self._user_id,
                    project_id=self._project_id,
                )


class CanvasGenerationHubSubscriber:
    """Subscribe generation hub for the turn lifetime; fan out patches/progress."""

    barrier_events = frozenset()
    broadcast_events = frozenset()

    def __init__(self, *, episode_id: int) -> None:
        self._episode_id = episode_id
        self._queue: asyncio.Queue[dict[str, Any] | None] | None = None
        self._fanout_task: asyncio.Task[None] | None = None
        self._emit: TurnEmit | None = None
        self._turn_id: str | None = None

    async def start(self, *, emit: TurnEmit, turn_id: str) -> None:
        self._emit = emit
        self._turn_id = turn_id
        self._queue = canvas_generation_hub.subscribe(self._episode_id)
        self._fanout_task = asyncio.create_task(self._fanout(), name=f"canvas-gen-fanout-{turn_id}")

    async def close(self) -> None:
        if self._fanout_task is not None:
            self._fanout_task.cancel()
            try:
                await self._fanout_task
            except asyncio.CancelledError:
                pass
            self._fanout_task = None
        if self._queue is not None:
            canvas_generation_hub.unsubscribe(self._episode_id, self._queue)
            await self._queue.put(None)
            self._queue = None

    async def handle(self, event: TurnEvent, *, emit: TurnEmit) -> None:
        return

    async def _fanout(self) -> None:
        assert self._queue is not None
        assert self._emit is not None
        try:
            while True:
                item = await self._queue.get()
                if item is None:
                    break
                patch = item.get("canvas_patch")
                if patch:
                    await self._emit(
                        create_stream_frame(
                            type=StreamFrameType.CANVAS_PATCH,
                            data=patch,
                            turn_id=self._turn_id,
                        )
                    )
                progress = item.get("progress")
                if progress:
                    await self._emit(
                        create_stream_frame(
                            type=StreamFrameType.GENERATION_PROGRESS,
                            turn_id=self._turn_id,
                            data=progress,
                        )
                    )
        except asyncio.CancelledError:
            return


class CanvasResumeSubscriber:
    """Resume path: tool patches. DONE owned by SseTurnSubscriber."""

    barrier_events = frozenset({TurnEventKind.TURN_COMPLETED})
    broadcast_events = frozenset({TurnEventKind.TOOL_FINISHED})

    async def handle(self, event: TurnEvent, *, emit: TurnEmit) -> None:
        if isinstance(event, ToolFinished):
            await _emit_canvas_patch_from_tool(event, emit)
            return
