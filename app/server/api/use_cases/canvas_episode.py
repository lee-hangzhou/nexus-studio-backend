from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Protocol
from uuid import uuid4

from app.agent.canvas.node_execution.manual_generate import run_manual_node_generate
from app.agent.canvas.services.session_checkpoint import delete_canvas_session_checkpoint
from app.agent.canvas.turn.lock import ActiveCanvasTurn, canvas_turn_lock
from app.agent.canvas.turn.orchestrator import stream_canvas_resume, stream_canvas_turn
from app.agent.canvas.turn.persistence import canvas_turn_already_completed
from app.agent.canvas.turn.replay_execution import run_canvas_replay_execution
from app.agent.runtime.background import background_supervisor
from app.agent.runtime.stream.replay import (
    ReplayMeta,
    ReplayRequestKind,
    build_request_fingerprint,
    replay_store,
    validate_replay_cursor,
)
from app.server.canvas.schemas.api import (
    CanvasNodeGenerateResponse,
    CanvasResumeRequest,
    CanvasSessionCreateRequest,
    CanvasSessionUpdateRequest,
    CanvasSessionView,
    CanvasTurnRequest,
)
from app.server.canvas.schemas.node_execute import SubmitNodeExecuteInput
from app.server.canvas.services.episode_events import publish_session_title
from app.server.canvas.services.episode_fence import canvas_episode_fence
from app.server.canvas.services.session_service import canvas_session_service
from app.server.exceptions.base import AppError
from app.server.exceptions.codes import ErrorCode
from app.server.infra.config import settings
from app.server.infra.logger import logger
from app.contracts.turn_content import (
    TurnUserInput,
    compile_human_text,
    extract_skill_paths,
    validate_turn_user_input,
)
from app.server.api.turn_input.enrich import enrich_turn_user_input
from app.server.ports.adapters import CanvasPortAdapter, UserSkillPortAdapter
from app.server.ports.product import CanvasPort, UserSkillPort
from app.server.projects.services.scope import CanvasScopeService
from app.server.projects.services.service import EpisodeService, canvas_scope_service, episode_service
from app.server.skills.domain.enums import SkillSurface
from app.server.skills.services import user_skill_service


class CanvasSessionLock(Protocol):
    async def acquire(
        self,
        session_id: int,
        turn_id: str,
        *,
        cancel_event: asyncio.Event | None = None,
    ) -> ActiveCanvasTurn | None: ...

    async def release(self, session_id: int, turn_id: str) -> bool: ...

    async def active_turn(self, session_id: int) -> str | None: ...

    async def cancel_and_wait(self, session_id: int, *, timeout_sec: float) -> str | None: ...

    async def force_release(self, session_id: int) -> str | None: ...


@dataclass(frozen=True, slots=True)
class CanvasTurnCancelResult:
    cancelled: bool
    active_turn_id: str | None


class CanvasEpisodeUseCases:
    """画布集级编排: session CRUD、turn、删集清理"""

    def __init__(
        self,
        *,
        scopes: CanvasScopeService,
        episodes: EpisodeService,
        lock: CanvasSessionLock,
        canvas: CanvasPort,
        user_skills: UserSkillPort,
    ) -> None:
        """注入 scope、episode、session turn 锁、画布 Port 与用户技能 Port"""
        self._scopes = scopes
        self._episodes = episodes
        self._lock = lock
        self._canvas = canvas
        self._user_skills = user_skills

    @asynccontextmanager
    async def _episode_mutex(self, episode_id: int, owner_prefix: str) -> AsyncIterator[None]:
        """持有集级 exclusive mutex 执行破坏性操作"""
        owner = f"{owner_prefix}:{uuid4().hex}"
        await canvas_episode_fence.acquire_exclusive(episode_id, owner)
        try:
            yield
        finally:
            await canvas_episode_fence.release_exclusive(episode_id, owner)

    async def _cancel_session_turn(self, *, episode_id: int, session_id: int) -> str | None:
        """取消并等待指定 session 的在途 turn"""
        active = await self._lock.active_turn(session_id)
        if active is None:
            return None
        await replay_store.signal_cancel(session_id=session_id, turn_id=active)
        logger.info(
            "canvas.turn.cancel_requested",
            episode_id=episode_id,
            session_id=session_id,
            turn_id=active,
        )
        try:
            await self._lock.cancel_and_wait(
                session_id,
                timeout_sec=settings.CANVAS_TURN_CANCEL_WAIT_SEC,
            )
        except AppError:
            raise
        return active

    async def _assert_episode_writable(self, episode_id: int) -> None:
        """删集持锁窗口内拒绝会话写入/生成/开 turn/改图"""
        await canvas_episode_fence.assert_writable(episode_id)

    async def delete_episode(self, *, user_id: int, episode_id: int) -> None:
        """删集: exclusive → 等生成排空 → busy → cancel → soft_close → checkpoint → 删数据"""
        scope = await self._scopes.require_write_scope(user_id, episode_id)
        async with self._episode_mutex(scope.episode_id, "delete"):
            await canvas_episode_fence.wait_generations_idle(
                scope.episode_id,
                timeout_sec=settings.CANVAS_TURN_CANCEL_WAIT_SEC,
            )
            await self._episodes.raise_if_canvas_busy(scope.episode_id)
            all_session_ids = await canvas_session_service.list_all_session_ids(scope.episode_id)
            active_ids = await canvas_session_service.list_active_session_ids(scope.episode_id)
            for session_id in active_ids:
                await self._cancel_session_turn(episode_id=scope.episode_id, session_id=session_id)
            await canvas_session_service.soft_close_all_for_episode(scope.episode_id)
            for session_id in all_session_ids:
                await delete_canvas_session_checkpoint(scope.episode_id, session_id)
            await self._episodes.delete(user_id, scope.episode_id, require_idle=False)

    async def generate_node(
        self,
        *,
        user_id: int,
        episode_id: int,
        node_id: str,
        body: SubmitNodeExecuteInput,
    ) -> CanvasNodeGenerateResponse:
        """手动节点生成: 删集窗口内由生成栅栏拒绝; 不占 turn 锁"""
        scope = await self._scopes.require_write_scope(user_id, episode_id)
        execute_input = body.model_copy(update={"node_id": node_id, "expected_revision": None})
        return await run_manual_node_generate(
            project_id=scope.project_id,
            episode_id=scope.episode_id,
            user_id=user_id,
            body=execute_input,
        )

    async def list_sessions(self, *, user_id: int, episode_id: int) -> list[CanvasSessionView]:
        """纯读列出当前用户 ACTIVE 会话"""
        scope = await self._scopes.require_read_scope(user_id, episode_id)
        return await canvas_session_service.list_sessions(scope)

    async def ensure_default_session(self, *, user_id: int, episode_id: int) -> CanvasSessionView:
        """写用例: 确保存在默认会话; 删集窗口内拒绝"""
        scope = await self._scopes.require_write_scope(user_id, episode_id)
        await self._assert_episode_writable(scope.episode_id)
        return await canvas_session_service.ensure_default_session(scope)

    async def create_session(
        self,
        *,
        user_id: int,
        episode_id: int,
        body: CanvasSessionCreateRequest,
    ) -> CanvasSessionView:
        """显式新建非默认会话"""
        scope = await self._scopes.require_write_scope(user_id, episode_id)
        await self._assert_episode_writable(scope.episode_id)
        return await canvas_session_service.create_session(scope, title=body.title)

    async def update_session(
        self,
        *,
        user_id: int,
        episode_id: int,
        body: CanvasSessionUpdateRequest,
    ) -> CanvasSessionView:
        """更新会话标题并广播; 删集窗口内拒绝"""
        scope = await self._scopes.require_write_scope(user_id, episode_id)
        await self._assert_episode_writable(scope.episode_id)
        view = await canvas_session_service.update_session(scope, body.session_id, title=body.title)
        await publish_session_title(
            scope.episode_id,
            session_id=view.id,
            title=view.title,
            updated_at=view.updated_at,
        )
        return view

    async def delete_session(self, *, user_id: int, episode_id: int, session_id: int) -> None:
        """关会话: 持集 mutex → cancel → 清 checkpoint → 软关; 保底默认会话"""
        scope = await self._scopes.require_write_scope(user_id, episode_id)
        async with self._episode_mutex(scope.episode_id, "delete_session"):
            row = await canvas_session_service.require_owned_session(scope, session_id)
            active_count = await canvas_session_service.count_active_for_user(scope)
            if active_count <= 1:
                raise AppError(ErrorCode.INVALID_PARAMS, "cannot close the last active canvas session")
            was_default = row.is_default
            await self._cancel_session_turn(episode_id=scope.episode_id, session_id=session_id)
            await delete_canvas_session_checkpoint(scope.episode_id, session_id)
            await canvas_session_service.soft_close_session(row)
            if was_default:
                await canvas_session_service.ensure_default_session(scope)

    async def start_turn(
        self,
        *,
        user_id: int,
        episode_id: int,
        body: CanvasTurnRequest,
        last_event_id: str | None,
    ) -> ReplayMeta:
        """启动 session 级 Agent turn 并返回可重放 meta"""
        scope = await self._scopes.require_write_scope(user_id, episode_id)
        await self._assert_episode_writable(scope.episode_id)
        session = await canvas_session_service.require_owned_session(scope, body.session_id)
        session_id = session.id
        if body.client_turn_id and await canvas_turn_already_completed(session_id, body.client_turn_id):
            raise AppError(
                ErrorCode.CANVAS_DUPLICATE_TURN,
                "canvas turn already completed",
                details={"client_turn_id": body.client_turn_id},
            )

        request_id = str(body.request_id)
        proposed_turn_id = uuid4().hex
        claim = await replay_store.claim(
            request_id=request_id,
            user_id=user_id,
            session_id=session_id,
            turn_id=proposed_turn_id,
            kind=ReplayRequestKind.TURN,
            fingerprint=build_request_fingerprint(
                kind=ReplayRequestKind.TURN,
                user_id=user_id,
                session_id=session_id,
                body=body,
            ),
        )
        try:
            validate_replay_cursor(claim.meta, last_event_id)
        except AppError:
            if claim.created:
                await replay_store.discard_starting(request_id)
            raise

        if claim.created:
            try:
                raw_input = validate_turn_user_input(
                    TurnUserInput(content=body.content, materials=body.materials),
                    allow_node=True,
                )
                user_input = await enrich_turn_user_input(
                    user_id=user_id,
                    user_input=raw_input,
                    project_id=scope.project_id,
                    episode_id=scope.episode_id,
                    canvas_port=self._canvas,
                    allow_node=True,
                )
                content_text = compile_human_text(user_input.content)
                skill_paths = extract_skill_paths(user_input.content)
                selected_skills = await self._user_skills.resolve_selected(
                    surface=SkillSurface.CANVAS,
                    user_id=user_id,
                    project_id=scope.project_id,
                    paths=skill_paths,
                )
            except ValueError as exc:
                await replay_store.discard_starting(request_id)
                raise AppError(ErrorCode.INVALID_PARAMS, str(exc)) from exc
            except AppError:
                await replay_store.discard_starting(request_id)
                raise
            except Exception:
                await replay_store.discard_starting(request_id)
                raise
            cancel_event = asyncio.Event()
            try:
                await self._lock.acquire(
                    session_id,
                    proposed_turn_id,
                    cancel_event=cancel_event,
                )
            except Exception:
                await replay_store.discard_starting(request_id)
                raise
            background_supervisor.start(
                run_canvas_replay_execution(
                    request_id=request_id,
                    project_id=scope.project_id,
                    episode_id=scope.episode_id,
                    session_id=session_id,
                    turn_id=proposed_turn_id,
                    cancel_event=cancel_event,
                    stream_factory=lambda: stream_canvas_turn(
                        project_id=scope.project_id,
                        episode_id=scope.episode_id,
                        session_id=session_id,
                        user_id=user_id,
                        content_text=content_text,
                        user_input=user_input,
                        selected_skills=selected_skills,
                        model_key=body.model_key,
                        client_turn_id=body.client_turn_id,
                        mode=body.mode,
                        enable_tools=body.enable_tools,
                        cancel_event=cancel_event,
                        turn_id=proposed_turn_id,
                        lock_held=True,
                    ),
                )
            )

        logger.info(
            "canvas.turn.replay_claim",
            project_id=scope.project_id,
            episode_id=scope.episode_id,
            session_id=session_id,
            request_id=request_id,
            turn_id=claim.meta.turn_id,
            created=claim.created,
            kind=ReplayRequestKind.TURN.value,
        )
        return claim.meta

    async def resume_turn(
        self,
        *,
        user_id: int,
        episode_id: int,
        body: CanvasResumeRequest,
        last_event_id: str | None,
    ) -> ReplayMeta:
        """恢复被中断的工具调用"""
        scope = await self._scopes.require_write_scope(user_id, episode_id)
        await self._assert_episode_writable(scope.episode_id)
        session = await canvas_session_service.require_owned_session(scope, body.session_id)
        session_id = session.id
        if not body.client_turn_id:
            raise AppError(ErrorCode.INVALID_PARAMS, "client_turn_id is required")
        turn_id = body.client_turn_id
        request_id = str(body.request_id)
        claim = await replay_store.claim(
            request_id=request_id,
            user_id=user_id,
            session_id=session_id,
            turn_id=turn_id,
            kind=ReplayRequestKind.RESUME,
            fingerprint=build_request_fingerprint(
                kind=ReplayRequestKind.RESUME,
                user_id=user_id,
                session_id=session_id,
                body=body,
            ),
        )
        try:
            validate_replay_cursor(claim.meta, last_event_id)
        except AppError:
            if claim.created:
                await replay_store.discard_starting(request_id)
            raise

        if claim.created:
            cancel_event = asyncio.Event()
            try:
                await self._lock.acquire(
                    session_id,
                    turn_id,
                    cancel_event=cancel_event,
                )
            except Exception:
                await replay_store.discard_starting(request_id)
                raise
            background_supervisor.start(
                run_canvas_replay_execution(
                    request_id=request_id,
                    project_id=scope.project_id,
                    episode_id=scope.episode_id,
                    session_id=session_id,
                    turn_id=turn_id,
                    cancel_event=cancel_event,
                    stream_factory=lambda: stream_canvas_resume(
                        project_id=scope.project_id,
                        episode_id=scope.episode_id,
                        session_id=session_id,
                        user_id=user_id,
                        turn_id=turn_id,
                        tool_call_id=body.tool_call_id,
                        action=body.action,
                        cancel_event=cancel_event,
                        lock_held=True,
                        model_key=body.model_key,
                        operation=body.operation,
                    ),
                )
            )

        logger.info(
            "canvas.turn.replay_claim",
            project_id=scope.project_id,
            episode_id=scope.episode_id,
            session_id=session_id,
            request_id=request_id,
            turn_id=claim.meta.turn_id,
            created=claim.created,
            kind=ReplayRequestKind.RESUME.value,
        )
        return claim.meta

    async def reconnect_turn(
        self,
        *,
        user_id: int,
        episode_id: int,
        session_id: int,
        request_id: str,
        last_event_id: str | None,
    ) -> ReplayMeta:
        """重连已有可重放 turn 流"""
        scope = await self._scopes.require_read_scope(user_id, episode_id)
        await canvas_session_service.require_owned_session(scope, session_id, allow_closed=True)
        meta = await replay_store.require_owned_meta(
            request_id,
            user_id=user_id,
            session_id=session_id,
        )
        logger.info(
            "canvas.turn.reconnect",
            project_id=scope.project_id,
            episode_id=scope.episode_id,
            session_id=session_id,
            request_id=meta.request_id,
            turn_id=meta.turn_id,
            last_event_id=last_event_id,
        )
        return meta

    async def cancel_turn(self, *, user_id: int, episode_id: int, session_id: int) -> CanvasTurnCancelResult:
        """取消当前 session 在途 turn"""
        scope = await self._scopes.require_write_scope(user_id, episode_id)
        await canvas_session_service.require_owned_session(scope, session_id)
        active = await self._cancel_session_turn(episode_id=scope.episode_id, session_id=session_id)
        if active is not None:
            logger.info(
                "canvas.turn.cancel_completed",
                project_id=scope.project_id,
                episode_id=scope.episode_id,
                session_id=session_id,
                turn_id=active,
            )
        return CanvasTurnCancelResult(cancelled=active is not None, active_turn_id=active)


canvas_episode_use_cases = CanvasEpisodeUseCases(
    scopes=canvas_scope_service,
    episodes=episode_service,
    lock=canvas_turn_lock,
    canvas=CanvasPortAdapter(),
    user_skills=UserSkillPortAdapter(user_skill_service),
)
