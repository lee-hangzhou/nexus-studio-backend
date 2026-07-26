from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Protocol
from uuid import uuid4

from app.agent.canvas.node_execution.manual_generate import run_manual_node_generate
from app.agent.canvas.turn.lock import ActiveCanvasTurn, canvas_turn_lock
from app.agent.canvas.turn.orchestrator import stream_canvas_resume, stream_canvas_turn
from app.agent.canvas.turn.persistence import canvas_turn_already_completed
from app.agent.canvas.turn.replay_execution import run_canvas_replay_execution
from app.agent.runtime.stream.replay import (
    ReplayMeta,
    ReplayRequestKind,
    build_request_fingerprint,
    execution_supervisor,
    replay_store,
    validate_replay_cursor,
)
from app.server.canvas.schemas.api import (
    CanvasNodeGenerateResponse,
    CanvasResumeRequest,
    CanvasTurnRequest,
)
from app.server.canvas.schemas.node_execute import SubmitNodeExecuteInput
from app.server.exceptions.base import AppError
from app.server.exceptions.codes import ErrorCode
from app.server.infra.config import settings
from app.server.infra.logger import logger
from app.server.projects.services.scope import CanvasScopeService
from app.server.projects.services.service import EpisodeService, canvas_scope_service, episode_service


class CanvasEpisodeLock(Protocol):
    async def acquire(
        self,
        episode_id: int,
        turn_id: str,
        *,
        cancel_event: asyncio.Event | None = None,
    ) -> ActiveCanvasTurn | None: ...

    async def release(self, episode_id: int, turn_id: str) -> bool: ...

    async def active_turn(self, episode_id: int) -> str | None: ...

    async def cancel_and_wait(self, episode_id: int, *, timeout_sec: float) -> str | None: ...


@dataclass(frozen=True, slots=True)
class CanvasTurnCancelResult:
    cancelled: bool
    active_turn_id: str | None


class CanvasEpisodeUseCases:
    def __init__(
        self,
        *,
        scopes: CanvasScopeService,
        episodes: EpisodeService,
        lock: CanvasEpisodeLock,
    ) -> None:
        self._scopes = scopes
        self._episodes = episodes
        self._lock = lock

    @asynccontextmanager
    async def _exclusive(self, episode_id: int, owner_prefix: str) -> AsyncIterator[None]:
        owner = f"{owner_prefix}:{uuid4().hex}"
        await self._lock.acquire(episode_id, owner)
        try:
            yield
        finally:
            await self._lock.release(episode_id, owner)

    async def delete_episode(self, *, user_id: int, episode_id: int) -> None:
        scope = await self._scopes.require_write_scope(user_id, episode_id)
        async with self._exclusive(scope.episode_id, "delete"):
            await self._episodes.delete(user_id, scope.episode_id)

    async def generate_node(
        self,
        *,
        user_id: int,
        episode_id: int,
        node_id: str,
        body: SubmitNodeExecuteInput,
    ) -> CanvasNodeGenerateResponse:
        scope = await self._scopes.require_write_scope(user_id, episode_id)
        execute_input = body.model_copy(update={"node_id": node_id, "expected_revision": None})
        async with self._exclusive(scope.episode_id, "manual_generate"):
            return await run_manual_node_generate(
                project_id=scope.project_id,
                episode_id=scope.episode_id,
                user_id=user_id,
                body=execute_input,
            )

    async def start_turn(
        self,
        *,
        user_id: int,
        episode_id: int,
        body: CanvasTurnRequest,
        last_event_id: str | None,
    ) -> ReplayMeta:
        scope = await self._scopes.require_write_scope(user_id, episode_id)
        if body.client_turn_id and await canvas_turn_already_completed(scope.episode_id, body.client_turn_id):
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
            session_id=scope.episode_id,
            turn_id=proposed_turn_id,
            kind=ReplayRequestKind.TURN,
            fingerprint=build_request_fingerprint(
                kind=ReplayRequestKind.TURN,
                user_id=user_id,
                session_id=scope.episode_id,
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
                    scope.episode_id,
                    proposed_turn_id,
                    cancel_event=cancel_event,
                )
            except Exception:
                await replay_store.discard_starting(request_id)
                raise
            execution_supervisor.start(
                run_canvas_replay_execution(
                    request_id=request_id,
                    project_id=scope.project_id,
                    episode_id=scope.episode_id,
                    turn_id=proposed_turn_id,
                    cancel_event=cancel_event,
                    stream_factory=lambda: stream_canvas_turn(
                        project_id=scope.project_id,
                        episode_id=scope.episode_id,
                        user_id=user_id,
                        content=body.content,
                        model_key=body.model_key or "",
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
        scope = await self._scopes.require_write_scope(user_id, episode_id)
        turn_id = body.client_turn_id or uuid4().hex
        request_id = str(body.request_id)
        claim = await replay_store.claim(
            request_id=request_id,
            user_id=user_id,
            session_id=scope.episode_id,
            turn_id=turn_id,
            kind=ReplayRequestKind.RESUME,
            fingerprint=build_request_fingerprint(
                kind=ReplayRequestKind.RESUME,
                user_id=user_id,
                session_id=scope.episode_id,
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
                    scope.episode_id,
                    turn_id,
                    cancel_event=cancel_event,
                )
            except Exception:
                await replay_store.discard_starting(request_id)
                raise
            execution_supervisor.start(
                run_canvas_replay_execution(
                    request_id=request_id,
                    project_id=scope.project_id,
                    episode_id=scope.episode_id,
                    turn_id=turn_id,
                    cancel_event=cancel_event,
                    stream_factory=lambda: stream_canvas_resume(
                        project_id=scope.project_id,
                        episode_id=scope.episode_id,
                        user_id=user_id,
                        turn_id=turn_id,
                        tool_call_id=body.tool_call_id,
                        action=body.action,
                        cancel_event=cancel_event,
                        lock_held=True,
                        model_key=body.model_key or "",
                    ),
                )
            )

        logger.info(
            "canvas.turn.replay_claim",
            project_id=scope.project_id,
            episode_id=scope.episode_id,
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
        request_id: str,
        last_event_id: str | None,
    ) -> ReplayMeta:
        scope = await self._scopes.require_read_scope(user_id, episode_id)
        meta = await replay_store.require_owned_meta(
            request_id,
            user_id=user_id,
            session_id=scope.episode_id,
        )
        logger.info(
            "canvas.turn.reconnect",
            project_id=scope.project_id,
            episode_id=scope.episode_id,
            request_id=meta.request_id,
            turn_id=meta.turn_id,
            last_event_id=last_event_id,
        )
        return meta

    async def cancel_turn(self, *, user_id: int, episode_id: int) -> CanvasTurnCancelResult:
        scope = await self._scopes.require_write_scope(user_id, episode_id)
        active = await self._lock.active_turn(scope.episode_id)
        if active is not None:
            await replay_store.signal_cancel(session_id=scope.episode_id, turn_id=active)
            logger.info(
                "canvas.turn.cancel_requested",
                project_id=scope.project_id,
                episode_id=scope.episode_id,
                turn_id=active,
            )
            await self._lock.cancel_and_wait(
                scope.episode_id,
                timeout_sec=settings.CANVAS_TURN_CANCEL_WAIT_SEC,
            )
            logger.info(
                "canvas.turn.cancel_completed",
                project_id=scope.project_id,
                episode_id=scope.episode_id,
                turn_id=active,
            )
        return CanvasTurnCancelResult(cancelled=active is not None, active_turn_id=active)


canvas_episode_use_cases = CanvasEpisodeUseCases(
    scopes=canvas_scope_service,
    episodes=episode_service,
    lock=canvas_turn_lock,
)
