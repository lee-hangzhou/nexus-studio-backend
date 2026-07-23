from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import aclosing
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from langchain_core.messages import BaseMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command

from app.agent.runtime.stream.encoder import encode_sse_frame
from app.agent.runtime.stream.frames import StreamFrame
from app.agent.runtime.agent.runner import run_agent_turn_stream
from app.agent.runtime.turn.guards import TurnGuards
from app.agent.runtime.turn.enums import TurnTerminatedBy
from app.agent.runtime.turn_engine.checkpoint import CheckpointRepairFn, run_checkpoint_repair
from app.agent.runtime.turn_engine.constants import TERMINATION_ERROR_MESSAGES
from app.agent.runtime.turn_engine.events import (
    ModelToken,
    TurnEnded,
    TurnFailed,
    TurnInterrupted,
    TurnStarted,
    TurnStarting,
)
from app.agent.runtime.turn_engine.handlers import BindableRecoveryHook, RecoveryHook, TurnEventHandlers
from app.agent.runtime.turn_engine.interrupts import collect_interrupt_values, collect_pending_tool_calls
from app.agent.runtime.turn_engine.run_state import RunState
from app.agent.runtime.turn_engine.sse_pump import STREAM_END_SENTINEL, pump_encoded_sse_queue
from app.agent.runtime.turn_engine.subscribers import TurnEmit, TurnEventBus, TurnSubscriber
from app.server.infra.logger import logger


class TurnCleanupTimeoutError(RuntimeError):
    def __init__(self, *, turn_id: str, timeout_sec: float) -> None:
        self.turn_id = turn_id
        self.timeout_sec = timeout_sec
        super().__init__(f"turn cleanup timed out after {timeout_sec:g}s")


@dataclass(frozen=True)
class TurnEngineConfig:
    thread_id: str
    runnable_config: RunnableConfig
    guards: TurnGuards
    heartbeat_interval_sec: int
    cancel_grace_sec: float = 2.0
    cleanup_timeout_sec: float = 10.0
    emit_invalid_tool_call_frames: bool = True
    heal_invalid_tool_calls: bool = True
    recovery_hook: RecoveryHook | None = None
    on_turn_start_repair: CheckpointRepairFn | None = None
    on_turn_cleanup_repair: CheckpointRepairFn | None = None


@dataclass(frozen=True)
class TurnEngineInput:
    turn_id: str
    conversation_id: int | str
    user_id: int | str
    input_messages: list[BaseMessage] | None = None
    resume_command: Command | None = None
    client_turn_id: str | None = None
    mode: str | None = None
    is_resume: bool = False


class TurnEngine:
    def __init__(self, subscribers: Sequence[TurnSubscriber] | None = None) -> None:
        self._bus = TurnEventBus(subscribers or ())

    async def stream(
        self,
        *,
        agent: CompiledStateGraph,
        turn_input: TurnEngineInput,
        config: TurnEngineConfig,
        cancel_event: asyncio.Event,
    ) -> AsyncIterator[str]:
        input_messages = list(turn_input.input_messages or [])
        for message in input_messages:
            if message.id is None:
                message.id = uuid4().hex

        out: asyncio.Queue[str | None] = asyncio.Queue()

        async def emit(frame: StreamFrame) -> None:
            chunk = encode_sse_frame(frame)
            await out.put(chunk)

        task = asyncio.create_task(
            self._run(
                agent=agent,
                turn_input=turn_input,
                input_messages=input_messages,
                config=config,
                cancel_event=cancel_event,
                emit=emit,
            ),
            name=f"turn-run-{turn_input.turn_id}",
        )
        task.add_done_callback(lambda _task: out.put_nowait(STREAM_END_SENTINEL))
        try:
            async with aclosing(
                pump_encoded_sse_queue(
                    out,
                    turn_id=turn_input.turn_id,
                    cancel_event=cancel_event,
                    heartbeat_interval_sec=config.heartbeat_interval_sec,
                )
            ) as pump:
                async for chunk in pump:
                    yield chunk
        finally:
            cancelled_while_waiting = False
            if not task.done():
                cancel_event.set()
                completed, was_cancelled = await self._wait_for_task(
                    task,
                    timeout_sec=config.cancel_grace_sec,
                )
                cancelled_while_waiting = cancelled_while_waiting or was_cancelled
                if not completed:
                    logger.warning(
                        "turn.engine.cancel_cooperative_timeout",
                        turn_id=turn_input.turn_id,
                        grace_sec=config.cancel_grace_sec,
                    )
                    logger.warning(
                        "turn.engine.cancel_forced",
                        turn_id=turn_input.turn_id,
                    )
                    task.cancel()

            if not task.done():
                _, was_cancelled = await self._wait_for_task(task)
                cancelled_while_waiting = cancelled_while_waiting or was_cancelled

            task_error: Exception | None = None
            try:
                task.result()
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                task_error = exc

            if task_error is not None:
                raise task_error
            if cancelled_while_waiting:
                raise asyncio.CancelledError

    @staticmethod
    async def _wait_for_task(
        task: asyncio.Task[None],
        *,
        timeout_sec: float | None = None,
    ) -> tuple[bool, bool]:
        deadline = None if timeout_sec is None else asyncio.get_running_loop().time() + timeout_sec
        cancelled_while_waiting = False
        while not task.done():
            timeout: float | None = None
            if deadline is not None:
                timeout = deadline - asyncio.get_running_loop().time()
                if timeout <= 0:
                    return False, cancelled_while_waiting
            try:
                done, _ = await asyncio.wait({task}, timeout=timeout)
            except asyncio.CancelledError:
                cancelled_while_waiting = True
                continue
            if done:
                break
            if deadline is not None:
                return False, cancelled_while_waiting
        return True, cancelled_while_waiting

    async def _run(
        self,
        *,
        agent: CompiledStateGraph,
        turn_input: TurnEngineInput,
        input_messages: list[BaseMessage],
        config: TurnEngineConfig,
        cancel_event: asyncio.Event,
        emit: TurnEmit,
    ) -> None:
        state = RunState()

        def broadcast(event) -> None:
            self._broadcast(event, emit=emit)

        hook = config.recovery_hook
        if isinstance(hook, BindableRecoveryHook):

            async def publish_answer_token(text: str) -> None:
                state.answer_parts.append(text)
                broadcast(
                    ModelToken(
                        turn_id=turn_input.turn_id,
                        step_index=0,
                        channel="answer",
                        text=text,
                    )
                )

            hook.bind_publish(publish_answer_token)

        handlers = TurnEventHandlers(
            turn_id=turn_input.turn_id,
            config=config,
            state=state,
            broadcast=broadcast,
            barrier=lambda event: self._barrier(event, emit=emit),
            fail=lambda **kwargs: self._fail(state=state, emit=emit, **kwargs),
            recovery_hook=config.recovery_hook,
        )
        try:
            await self._start_turn(agent, turn_input, input_messages, config, state, emit)
            await self._drive_agent_stream(
                handlers,
                state,
                turn_input,
                config,
                cancel_event,
                agent,
                emit,
            )
        except asyncio.CancelledError:
            if state.terminated_by not in (
                TurnTerminatedBy.COMPLETED,
                TurnTerminatedBy.INTERRUPTED,
            ):
                state.terminated_by = TurnTerminatedBy.CANCELLED
            raise
        except Exception as exc:
            await self._handle_run_exception(exc, turn_input.turn_id, state, emit)
        finally:
            await self._finish_turn_shielded(agent, turn_input, config, state, emit)

    async def _start_turn(
        self,
        agent: CompiledStateGraph,
        turn_input: TurnEngineInput,
        input_messages: list[BaseMessage],
        config: TurnEngineConfig,
        state: RunState,
        emit: TurnEmit,
    ) -> None:
        await self._bus.start(emit=emit, turn_id=turn_input.turn_id)
        await self._barrier(
            TurnStarting(
                turn_id=turn_input.turn_id,
                conversation_id=turn_input.conversation_id,
                user_id=turn_input.user_id,
                input_messages=input_messages,
                client_turn_id=turn_input.client_turn_id,
                mode=turn_input.mode,
                is_resume=turn_input.is_resume,
            ),
            emit=emit,
        )
        if not turn_input.is_resume:
            await run_checkpoint_repair(
                config.on_turn_start_repair,
                agent=agent,
                runnable_config=config.runnable_config,
                thread_id=config.thread_id,
                turn_id=turn_input.turn_id,
                reason="turn_start",
            )
        self._broadcast(
            TurnStarted(
                turn_id=turn_input.turn_id,
                thread_id=config.thread_id,
                config=config.runnable_config,
                started_at=datetime.now(UTC),
            ),
            emit=emit,
        )
        state.agent_started = True

    async def _drive_agent_stream(
        self,
        handlers: TurnEventHandlers,
        state: RunState,
        turn_input: TurnEngineInput,
        config: TurnEngineConfig,
        cancel_event: asyncio.Event,
        agent: CompiledStateGraph,
        emit: TurnEmit,
    ) -> None:
        if turn_input.resume_command is not None:
            graph_input: list[BaseMessage] | Command = turn_input.resume_command
        else:
            graph_input = list(turn_input.input_messages or [])

        async for event in run_agent_turn_stream(
            agent,
            graph_input,
            turn_id=turn_input.turn_id,
            config=config.runnable_config,
        ):
            if cancel_event.is_set():
                state.terminated_by = TurnTerminatedBy.CANCELLED
                break
            if not config.guards.check_wall_clock():
                state.terminated_by = TurnTerminatedBy.WALL_CLOCK
                break

            result = await handlers.dispatch(event)
            if result.action == "return":
                if result.terminated_by is not None:
                    state.terminated_by = result.terminated_by
                return
            if result.action == "break":
                if result.terminated_by is not None:
                    state.terminated_by = result.terminated_by
                break

        interrupted = await self._emit_interrupts(agent, config.runnable_config, turn_input.turn_id, emit=emit)
        if interrupted:
            state.terminated_by = TurnTerminatedBy.INTERRUPTED
            return
        if state.terminated_by == TurnTerminatedBy.COMPLETED or cancel_event.is_set():
            return
        if config.recovery_hook is not None:
            recovered = await config.recovery_hook.on_stream_break(
                state.terminated_by,
                state=state,
                agent=agent,
                runnable_config=config.runnable_config,
            )
            if recovered is not None:
                state.terminated_by = recovered
                if recovered == TurnTerminatedBy.COMPLETED:
                    return
                if recovered == TurnTerminatedBy.CANCELLED:
                    return
                if recovered == TurnTerminatedBy.AGENT_RECOVERY_EXHAUSTED:
                    await self._fail(
                        turn_id=turn_input.turn_id,
                        error=TERMINATION_ERROR_MESSAGES.get(
                            recovered,
                            str(recovered),
                        ),
                        error_class=recovered.value,
                        emit=emit,
                        state=state,
                    )
                    return
        if state.terminated_by != TurnTerminatedBy.COMPLETED and not cancel_event.is_set():
            await self._fail(
                turn_id=turn_input.turn_id,
                error=TERMINATION_ERROR_MESSAGES.get(state.terminated_by, str(state.terminated_by)),
                error_class=state.terminated_by.value,
                emit=emit,
                state=state,
            )

    async def _handle_run_exception(
        self,
        exc: Exception,
        turn_id: str,
        state: RunState,
        emit: TurnEmit,
    ) -> None:
        logger.exception("turn.engine.error", turn_id=turn_id, error=str(exc))
        state.terminated_by = TurnTerminatedBy.INTERNAL
        try:
            await self._fail(
                turn_id=turn_id,
                error=str(exc),
                error_class=TurnTerminatedBy.INTERNAL.value,
                emit=emit,
                state=state,
            )
        except Exception as fail_exc:
            logger.exception("turn.engine.fail_emit_failed", turn_id=turn_id, error=str(fail_exc))

    async def _finish_turn(
        self,
        agent: CompiledStateGraph,
        turn_input: TurnEngineInput,
        config: TurnEngineConfig,
        state: RunState,
        emit: TurnEmit,
    ) -> None:
        if state.agent_started and state.terminated_by not in (
            TurnTerminatedBy.COMPLETED,
            TurnTerminatedBy.INTERRUPTED,
        ):
            await run_checkpoint_repair(
                config.on_turn_cleanup_repair,
                agent=agent,
                runnable_config=config.runnable_config,
                thread_id=config.thread_id,
                turn_id=turn_input.turn_id,
                reason=state.terminated_by.value,
            )
        self._broadcast(
            TurnEnded(
                turn_id=turn_input.turn_id,
                terminated_by=state.terminated_by,
                failed_emitted=state.failed_emitted,
            ),
            emit=emit,
        )
        await self._bus.close()

    async def _finish_turn_shielded(
        self,
        agent: CompiledStateGraph,
        turn_input: TurnEngineInput,
        config: TurnEngineConfig,
        state: RunState,
        emit: TurnEmit,
    ) -> None:
        cleanup_task = asyncio.create_task(
            self._finish_turn(agent, turn_input, config, state, emit),
            name=f"turn-cleanup-{turn_input.turn_id}",
        )
        cancelled_while_waiting = False
        completed, was_cancelled = await self._wait_for_task(
            cleanup_task,
            timeout_sec=config.cleanup_timeout_sec,
        )
        cancelled_while_waiting = cancelled_while_waiting or was_cancelled
        if not completed:
            logger.error(
                "turn.engine.cleanup_timeout",
                turn_id=turn_input.turn_id,
                timeout_sec=config.cleanup_timeout_sec,
            )
            cleanup_task.cancel()
            cleanup_task.add_done_callback(
                lambda done: self._consume_timed_out_cleanup_result(done, turn_id=turn_input.turn_id)
            )
            raise TurnCleanupTimeoutError(
                turn_id=turn_input.turn_id,
                timeout_sec=config.cleanup_timeout_sec,
            )
        cleanup_task.result()
        if cancelled_while_waiting:
            raise asyncio.CancelledError

    @staticmethod
    def _consume_timed_out_cleanup_result(task: asyncio.Task[None], *, turn_id: str) -> None:
        try:
            task.result()
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.exception(
                "turn.engine.cleanup_cancel_failed",
                turn_id=turn_id,
                error=str(exc),
            )

    async def _fail(
        self,
        *,
        turn_id: str,
        error: str,
        error_class: str | None,
        emit: TurnEmit,
        state: RunState,
        step_index: int | None = None,
    ) -> None:
        if state.failed_emitted:
            return
        state.failed_emitted = True
        await self._barrier(
            TurnFailed(
                turn_id=turn_id,
                error=error,
                error_class=error_class,
                step_index=step_index,
            ),
            emit=emit,
        )

    async def _barrier(self, event, *, emit: TurnEmit) -> None:
        await self._bus.barrier(event, emit=emit)

    def _broadcast(self, event, *, emit: TurnEmit) -> None:
        self._bus.broadcast(event, emit=emit)

    async def _emit_interrupts(
        self,
        agent: CompiledStateGraph,
        config: RunnableConfig,
        turn_id: str,
        *,
        emit: TurnEmit,
    ) -> bool:
        values = await collect_interrupt_values(agent, config)
        if not values:
            return False
        pending_tool_calls = await collect_pending_tool_calls(agent, config)
        await self._barrier(
            TurnInterrupted(
                turn_id=turn_id,
                interrupts=values,
                pending_tool_calls=pending_tool_calls,
            ),
            emit=emit,
        )
        return True
