"""Canvas turn side effects: persistence, patches, generation hub fanout."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any

from app.agent.canvas.turn.generation_hub import canvas_generation_hub
from app.agent.canvas.turn.memory_background import schedule_canvas_memory_extract
from app.agent.canvas.turn.persistence import (
    persist_canvas_assistant_message,
    persist_canvas_tool_step,
    persist_canvas_user_message,
)
from app.agent.runtime.stream.frames import StreamFrameType, create_stream_frame
from app.agent.runtime.tools.result import ToolResult
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
from app.server.projects.persistence.projects import Projects


def _parse_tool_payload(raw: str) -> dict[str, Any] | None:
    parsed = ToolResult.parse_tool_message(raw)
    if not parsed.success:
        return None
    try:
        return json.loads(parsed.output or "{}")
    except json.JSONDecodeError:
        return None


async def _emit_canvas_patch_from_tool(event: ToolFinished, emit: TurnEmit) -> None:
    name = event.tool_name or ""
    if name not in ("apply_canvas_patch", "submit_node_generation"):
        return
    raw = event.tool_result or ""
    parsed = ToolResult.parse_tool_message(raw)
    if not parsed.success:
        return
    data = _parse_tool_payload(raw)
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


async def _touch_project(project_id: int) -> None:
    await Projects.filter(id=project_id).update(updated_at=datetime.now(timezone.utc))


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
        user_id: int,
        content: str,
        client_turn_id: str | None,
        enable_tools: bool,
        persist_user: bool = True,
        schedule_memory: bool = True,
    ) -> None:
        self._project_id = project_id
        self._user_id = user_id
        self._content = content
        self._client_turn_id = client_turn_id
        self._enable_tools = enable_tools
        self._persist_user = persist_user
        self._schedule_memory = schedule_memory

    async def handle(self, event: TurnEvent, *, emit: TurnEmit) -> None:
        if isinstance(event, TurnStarting) and self._persist_user:
            await persist_canvas_user_message(
                project_id=self._project_id,
                user_id=self._user_id,
                content=self._content,
                client_turn_id=self._client_turn_id,
                turn_id=event.turn_id,
            )
            return

        if isinstance(event, ToolFinished) and self._enable_tools:
            await persist_canvas_tool_step(
                project_id=self._project_id,
                user_id=self._user_id,
                turn_id=event.turn_id,
                step=CanvasToolStepMetadata(
                    call_id=event.call_id,
                    name=event.tool_name,
                    ok=not event.tool_error,
                    preview=event.tool_result[:500],
                    error_type=event.error_class,
                ),
            )
            await _emit_canvas_patch_from_tool(event, emit)
            return

        if isinstance(event, TurnFailed):
            await _touch_project(self._project_id)
            return

        if isinstance(event, TurnCompleted):
            answer_text = (event.answer_text or "").strip()
            if not answer_text:
                # Empty answers fail in CanvasEmptyAnswerHook before TurnCompleted.
                return

            await persist_canvas_assistant_message(
                project_id=self._project_id,
                user_id=self._user_id,
                content=answer_text,
                client_turn_id=self._client_turn_id,
                turn_id=event.turn_id,
                tool_calls_count=event.tool_calls_count,
            )
            await _touch_project(self._project_id)
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

    def __init__(self, *, project_id: int) -> None:
        self._project_id = project_id
        self._queue: asyncio.Queue[dict[str, Any] | None] | None = None
        self._fanout_task: asyncio.Task[None] | None = None
        self._emit: TurnEmit | None = None
        self._turn_id: str | None = None

    async def start(self, *, emit: TurnEmit, turn_id: str) -> None:
        self._emit = emit
        self._turn_id = turn_id
        self._queue = canvas_generation_hub.subscribe(self._project_id)
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
            canvas_generation_hub.unsubscribe(self._project_id, self._queue)
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
