from __future__ import annotations

from app.agent.canvas.turn.memory_background import schedule_canvas_memory_extract
from app.agent.canvas.turn.persistence import (
    persist_canvas_assistant_message,
    persist_canvas_tool_step,
    persist_canvas_user_message,
)
from app.agent.canvas.turn.session_title import (
    is_canvas_auto_title_eligible,
    schedule_canvas_session_title,
)
from app.agent.chat.tools.ui_preview import sanitize_tool_step_preview
from app.agent.runtime.ports import get_canvas_port
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


async def _touch_episode(episode_id: int) -> None:
    """刷新集 updated_at"""
    await get_canvas_port().touch_episode(episode_id)


class CanvasPersistenceSubscriber:
    """持久化用户/助手/工具步骤; DONE/ERROR 由 SseTurnSubscriber 负责"""

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
        session_id: int,
        user_id: int,
        content: str,
        client_turn_id: str | None,
        enable_tools: bool,
        model_key: str = "",
        persist_user: bool = True,
        schedule_memory: bool = True,
    ) -> None:
        self._project_id = project_id
        self._episode_id = episode_id
        self._session_id = session_id
        self._user_id = user_id
        self._content = content
        self._client_turn_id = client_turn_id
        self._enable_tools = enable_tools
        self._model_key = model_key
        self._persist_user = persist_user
        self._schedule_memory = schedule_memory

    async def handle(self, event: TurnEvent, *, emit: TurnEmit) -> None:
        """按 turn 事件持久化并在首轮调度标题"""
        if isinstance(event, TurnStarting) and self._persist_user:
            await persist_canvas_user_message(
                episode_id=self._episode_id,
                session_id=self._session_id,
                user_id=self._user_id,
                content=self._content,
                client_turn_id=self._client_turn_id,
                turn_id=event.turn_id,
            )
            await get_canvas_port().touch_session(
                episode_id=self._episode_id,
                session_id=self._session_id,
                user_id=self._user_id,
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
                session_id=self._session_id,
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
            return

        if isinstance(event, TurnFailed):
            await self._maybe_schedule_session_title()
            await _touch_episode(self._episode_id)
            return

        if isinstance(event, TurnCompleted):
            await self._maybe_schedule_session_title()
            answer_text = (event.answer_text or "").strip()
            if not answer_text:
                return

            await persist_canvas_assistant_message(
                episode_id=self._episode_id,
                session_id=self._session_id,
                user_id=self._user_id,
                content=answer_text,
                client_turn_id=self._client_turn_id,
                turn_id=event.turn_id,
                tool_calls_count=event.tool_calls_count,
            )
            await _touch_episode(self._episode_id)
            if self._schedule_memory:
                schedule_canvas_memory_extract(
                    user_text=self._content,
                    answer_text=answer_text,
                    user_id=self._user_id,
                    project_id=self._project_id,
                    turn_id=event.turn_id,
                    turn_model_key=self._model_key,
                )

    async def _maybe_schedule_session_title(self) -> None:
        """首轮且标题仍为占位时后台生成标题"""
        if not self._model_key or not self._content.strip():
            return
        canvas = get_canvas_port()
        if await canvas.count_session_user_messages(self._session_id) != 1:
            return
        current = await canvas.get_session_title(
            episode_id=self._episode_id,
            session_id=self._session_id,
            user_id=self._user_id,
        )
        if current is None or not is_canvas_auto_title_eligible(current):
            return
        schedule_canvas_session_title(
            episode_id=self._episode_id,
            session_id=self._session_id,
            user_id=self._user_id,
            user_content=self._content,
            model_key=self._model_key,
        )


class CanvasResumeSubscriber:
    """Resume 路径: 只持久化工具步骤; DONE 由 SseTurnSubscriber 负责"""

    barrier_events = frozenset({TurnEventKind.TURN_COMPLETED})
    broadcast_events = frozenset({TurnEventKind.TOOL_FINISHED})

    def __init__(
        self,
        *,
        episode_id: int,
        session_id: int,
        user_id: int,
    ) -> None:
        self._episode_id = episode_id
        self._session_id = session_id
        self._user_id = user_id

    async def handle(self, event: TurnEvent, *, emit: TurnEmit) -> None:
        """Resume 时写入工具步骤"""
        if isinstance(event, ToolFinished):
            preview = sanitize_tool_step_preview(
                event.tool_name,
                event.tool_result,
                ok=not event.tool_error,
            )
            await persist_canvas_tool_step(
                episode_id=self._episode_id,
                session_id=self._session_id,
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
            return
