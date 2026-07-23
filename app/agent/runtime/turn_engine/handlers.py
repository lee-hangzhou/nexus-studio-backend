from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal, Protocol, runtime_checkable

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langgraph.graph.state import CompiledStateGraph
from langchain_core.runnables import RunnableConfig

from app.agent.runtime.agent.events import (
    AgentEvent,
    AgentEventType,
    InvalidToolCall,
    ModelStepFinishedEvent,
    ModelTokenEvent,
    ToolFinishedEvent,
    ToolStartedEvent,
    TurnCompletedEvent,
    TurnFailedEvent,
)
from app.agent.runtime.agent.gateway_fail import terminated_by_for_error_class
from app.agent.runtime.tools.result import INVALID_ARGUMENTS, ToolResult
from app.agent.runtime.turn.enums import GuardAction, TurnTerminatedBy
from app.agent.runtime.turn.guards import TurnGuards
from app.agent.runtime.turn_engine.constants import INVALID_TOOL_RAW_PREVIEW_LEN, TERMINATION_ERROR_MESSAGES
from app.agent.runtime.turn_engine.events import (
    ModelStepFinished,
    ModelToken,
    ToolFinished,
    ToolStarted,
    TurnCompleted,
    TurnEvent,
)
from app.agent.runtime.turn_engine.run_state import RunState
from app.agent.runtime.stream.text import chunk_text
from app.server.infra.logger import logger

TurnBroadcast = Callable[[TurnEvent], None]
TurnBarrier = Callable[[TurnEvent], Awaitable[None]]
PublishAnswerToken = Callable[[str], Awaitable[None]]


class TurnHandlerConfig(Protocol):
    guards: TurnGuards
    emit_invalid_tool_call_frames: bool
    heal_invalid_tool_calls: bool


class TurnFail(Protocol):
    async def __call__(
        self,
        *,
        turn_id: str,
        error: str,
        error_class: str | None,
        step_index: int | None = None,
    ) -> None: ...


@dataclass(frozen=True)
class HandlerResult:
    action: Literal["continue", "break", "return"]
    terminated_by: TurnTerminatedBy | None = None


class RecoveryHook(Protocol):
    """Recovery strategy — may persist / publish answer tokens; never emits terminal SSE."""

    async def after_model_step(
        self,
        event: ModelStepFinishedEvent,
        *,
        state: RunState,
    ) -> TurnTerminatedBy | None: ...

    async def before_complete(
        self,
        event: TurnCompletedEvent,
        *,
        state: RunState,
    ) -> TurnTerminatedBy | None: ...

    async def on_stream_break(
        self,
        terminated_by: TurnTerminatedBy,
        *,
        state: RunState,
        agent: CompiledStateGraph,
        runnable_config: RunnableConfig,
    ) -> TurnTerminatedBy | None: ...


@runtime_checkable
class BindableRecoveryHook(Protocol):
    def bind_publish(self, publish_answer_token: PublishAnswerToken) -> None: ...


def _message_text(message: AIMessage) -> str:
    return str(message.content or "").strip()


def _current_turn_message_slice(messages: list[BaseMessage]) -> list[BaseMessage]:
    last_human = -1
    for index, message in enumerate(messages):
        if isinstance(message, HumanMessage):
            last_human = index
    if last_human < 0:
        return list(messages)
    return list(messages)[last_human + 1 :]


def broadcast_invalid_tool_call(
    inv: InvalidToolCall,
    *,
    turn_id: str,
    step_index: int,
    broadcast: TurnBroadcast,
) -> None:
    detail = (
        f"参数解析失败: {inv.parse_error}\n"
        f"raw: {inv.raw_arguments[:INVALID_TOOL_RAW_PREVIEW_LEN]}"
    )
    envelope = ToolResult.fail(INVALID_ARGUMENTS, detail=detail).to_tool_message()
    broadcast(
        ToolStarted(
            turn_id=turn_id,
            step_index=step_index,
            call_id=inv.call_id,
            tool_name=inv.name,
            tool_args={"parse_error": inv.parse_error},
            synthetic=True,
        ),
    )
    broadcast(
        ToolFinished(
            turn_id=turn_id,
            step_index=step_index,
            call_id=inv.call_id,
            tool_name=inv.name,
            tool_result=envelope,
            tool_error=True,
            error_class=INVALID_ARGUMENTS,
            synthetic=True,
        ),
    )


async def backfill_step_answer(
    ai_message: AIMessage,
    *,
    answer_parts: list[str],
    broadcast: TurnBroadcast,
    turn_id: str,
    step_index: int,
) -> None:
    step_answer = _message_text(ai_message)
    if not step_answer or ai_message.tool_calls or answer_parts:
        return
    for piece in chunk_text(step_answer):
        answer_parts.append(piece)
        broadcast(
            ModelToken(
                turn_id=turn_id,
                step_index=step_index,
                channel="answer",
                text=piece,
            ),
        )


async def backfill_final_answer(
    messages: list[BaseMessage],
    *,
    answer_parts: list[str],
    broadcast: TurnBroadcast,
    turn_id: str,
) -> None:
    if answer_parts:
        return
    for message in reversed(_current_turn_message_slice(messages)):
        if not isinstance(message, AIMessage):
            continue
        if message.tool_calls:
            continue
        text = _message_text(message)
        if not text:
            continue
        for piece in chunk_text(text):
            answer_parts.append(piece)
            broadcast(
                ModelToken(
                    turn_id=turn_id,
                    step_index=0,
                    channel="answer",
                    text=piece,
                ),
            )
        return


@dataclass
class TurnEventHandlers:
    turn_id: str
    config: TurnHandlerConfig
    state: RunState
    broadcast: TurnBroadcast
    barrier: TurnBarrier
    fail: TurnFail
    recovery_hook: RecoveryHook | None = None

    async def dispatch(self, event: AgentEvent) -> HandlerResult:
        if event.type == AgentEventType.MODEL_TOKEN:
            return self._handle_model_token(event)
        if event.type == AgentEventType.TOOL_STARTED:
            return self._handle_tool_started(event)
        if event.type == AgentEventType.MODEL_STEP_FINISHED:
            return await self._handle_model_step_finished(event)
        if event.type == AgentEventType.TOOL_FINISHED:
            return self._handle_tool_finished(event)
        if event.type == AgentEventType.TURN_FAILED:
            return await self._handle_turn_failed(event)
        if event.type == AgentEventType.TURN_COMPLETED:
            return await self._handle_turn_completed(event)
        return HandlerResult(action="continue")

    def _handle_model_token(self, event: ModelTokenEvent) -> HandlerResult:
        if event.channel == "answer":
            self.state.answer_parts.append(event.text)
        self.broadcast(
            ModelToken(
                turn_id=self.turn_id,
                step_index=event.step_index,
                channel=event.channel,
                text=event.text,
            ),
        )
        return HandlerResult(action="continue")

    def _handle_tool_started(self, event: ToolStartedEvent) -> HandlerResult:
        self.broadcast(
            ToolStarted(
                turn_id=self.turn_id,
                step_index=event.step_index,
                call_id=event.call_id,
                tool_name=event.tool_name,
                tool_args=event.tool_args,
            ),
        )
        return HandlerResult(action="continue")

    async def _handle_model_step_finished(self, event: ModelStepFinishedEvent) -> HandlerResult:
        guards = self.config.guards
        guards.on_model_step_finished()
        self.broadcast(
            ModelStepFinished(
                turn_id=self.turn_id,
                step_index=event.step_index,
                ai_message=event.ai_message,
                invalid_tool_calls=event.invalid_tool_calls,
            ),
        )
        if guards.exceeded_model_steps():
            return HandlerResult(action="break", terminated_by=TurnTerminatedBy.MAX_ITERATIONS)

        if event.invalid_tool_calls:
            if self.config.emit_invalid_tool_call_frames:
                for inv in event.invalid_tool_calls:
                    broadcast_invalid_tool_call(
                        inv,
                        turn_id=self.turn_id,
                        step_index=event.step_index,
                        broadcast=self.broadcast,
                    )
            valid_calls = list(event.ai_message.tool_calls or [])
            if not valid_calls:
                first = event.invalid_tool_calls[0]
                if self.config.heal_invalid_tool_calls:
                    logger.warning(
                        "turn.tool_parse_healed",
                        turn_id=self.turn_id,
                        tool_name=first.name,
                        parse_error=first.parse_error,
                    )
                    # Do not fail the turn — let completion / recovery produce a user answer.
                else:
                    logger.error(
                        "turn.tool_parse_fatal",
                        turn_id=self.turn_id,
                        tool_name=first.name,
                        parse_error=first.parse_error,
                    )
                    await self.fail(
                        turn_id=self.turn_id,
                        error=f"工具参数解析失败（{first.name}）",
                        error_class=TurnTerminatedBy.TOOL_PARSE_FATAL.value,
                        step_index=event.step_index,
                    )
                    return HandlerResult(
                        action="return",
                        terminated_by=TurnTerminatedBy.TOOL_PARSE_FATAL,
                    )

        if self.recovery_hook is not None:
            hook_result = await self.recovery_hook.after_model_step(event, state=self.state)
            if hook_result is not None:
                await self.fail(
                    turn_id=self.turn_id,
                    error=_termination_message(hook_result),
                    error_class=hook_result.value,
                    step_index=event.step_index,
                )
                return HandlerResult(action="return", terminated_by=hook_result)

        await backfill_step_answer(
            event.ai_message,
            answer_parts=self.state.answer_parts,
            broadcast=self.broadcast,
            turn_id=self.turn_id,
            step_index=event.step_index,
        )
        return HandlerResult(action="continue")

    def _handle_tool_finished(self, event: ToolFinishedEvent) -> HandlerResult:
        self.broadcast(
            ToolFinished(
                turn_id=self.turn_id,
                step_index=event.step_index,
                call_id=event.call_id,
                tool_name=event.tool_name,
                tool_result=event.tool_result,
                tool_error=event.tool_error,
                error_class=event.error_class,
            ),
        )
        self.state.tool_calls_count += 1
        action = self.config.guards.on_tool_finished(event.tool_name, event.error_class)
        if action == GuardAction.BROWSER_BLOCKED:
            return HandlerResult(action="break", terminated_by=TurnTerminatedBy.COMPLETED)
        if action == GuardAction.STOP_TURN:
            stop_reason = self.config.guards.last_stop_reason
            terminated_by = (
                TurnTerminatedBy.MAX_TOOLS
                if stop_reason == "max_tools"
                else TurnTerminatedBy.GUARD_ERROR
            )
            return HandlerResult(action="break", terminated_by=terminated_by)
        return HandlerResult(action="continue")

    async def _handle_turn_failed(self, event: TurnFailedEvent) -> HandlerResult:
        terminated_by = terminated_by_for_error_class(event.error_class)
        await self.fail(
            turn_id=self.turn_id,
            error=event.error,
            error_class=event.error_class,
            step_index=event.step_index,
        )
        return HandlerResult(action="return", terminated_by=terminated_by)

    async def _handle_turn_completed(self, event: TurnCompletedEvent) -> HandlerResult:
        messages = list(event.messages)
        await backfill_final_answer(
            messages,
            answer_parts=self.state.answer_parts,
            broadcast=self.broadcast,
            turn_id=self.turn_id,
        )
        if self.recovery_hook is not None:
            hook_result = await self.recovery_hook.before_complete(event, state=self.state)
            if hook_result is not None:
                if hook_result == TurnTerminatedBy.CANCELLED:
                    return HandlerResult(action="return", terminated_by=TurnTerminatedBy.CANCELLED)
                if hook_result == TurnTerminatedBy.INTERRUPTED:
                    # Explicit interrupt: broadcast TurnCompleted so Chat persistence can emit
                    # USER_GATE and set force_interrupted. Do not fail() — not a gateway error.
                    completed = TurnCompleted(
                        turn_id=self.turn_id,
                        messages=messages,
                        answer_text="".join(self.state.answer_parts),
                        tool_calls_count=self.state.tool_calls_count,
                    )
                    await self.barrier(completed)
                    self.broadcast(completed)
                    self.state.terminated_by = TurnTerminatedBy.INTERRUPTED
                    return HandlerResult(
                        action="return",
                        terminated_by=TurnTerminatedBy.INTERRUPTED,
                    )
                await self.fail(
                    turn_id=self.turn_id,
                    error=_termination_message(hook_result),
                    error_class=hook_result.value,
                    step_index=event.step_index,
                )
                return HandlerResult(action="return", terminated_by=hook_result)

        completed = TurnCompleted(
            turn_id=self.turn_id,
            messages=messages,
            answer_text="".join(self.state.answer_parts),
            tool_calls_count=self.state.tool_calls_count,
        )
        await self.barrier(completed)
        self.broadcast(completed)
        force_failed = getattr(self.recovery_hook, "force_failed", None) if self.recovery_hook else None
        if isinstance(force_failed, tuple) and len(force_failed) == 2:
            error, error_class = force_failed
            await self.fail(
                turn_id=self.turn_id,
                error=str(error),
                error_class=str(error_class),
                step_index=event.step_index,
            )
            return HandlerResult(action="return", terminated_by=TurnTerminatedBy.TURN_FAILED)
        if self.recovery_hook is not None and getattr(
            self.recovery_hook, "force_interrupted", False
        ):
            self.state.terminated_by = TurnTerminatedBy.INTERRUPTED
            return HandlerResult(action="return", terminated_by=TurnTerminatedBy.INTERRUPTED)
        self.state.terminated_by = TurnTerminatedBy.COMPLETED
        return HandlerResult(action="continue", terminated_by=TurnTerminatedBy.COMPLETED)


def _termination_message(terminated_by: TurnTerminatedBy) -> str:
    return TERMINATION_ERROR_MESSAGES.get(terminated_by, str(terminated_by))
