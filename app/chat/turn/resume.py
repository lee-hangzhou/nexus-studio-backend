"""Resume chat turn after UserGate interrupt."""

from __future__ import annotations

import asyncio
import contextlib
import time
from typing import Any, AsyncIterator

from langchain_core.messages import BaseMessage
from langgraph.types import Command

from app.chat.agent.events import AgentEventType
from app.chat.agent.factory import build_chat_agent
from app.chat.stream.agent_frames import frames_from_agent_event
from app.chat.agent.runner import run_agent_turn_stream
from app.chat.gate import assets as gate_assets
from app.chat.gate import meta as gate_meta_store
from app.chat.gate.pending import clear_gate_pending, get_gate_pending
from app.chat.gate import turn_auth as turn_auth_store
from app.chat.llm.gateway_chat_model import GatewayChatModel
from app.chat.llm.registry import get_model_spec
from app.chat.memory.store import chat_runnable_config
from app.chat.mcp.client import load_mcp_tools
from app.chat.stream.encoder import encode_sse_frame
from app.chat.stream.frames import StreamFrame, StreamFrameType, create_stream_frame
from app.chat.tools.browser_names import REQUEST_USER_GATE
from app.chat.tools.lc_tools import ChatToolContext, build_langchain_tools
from app.chat.tools.result import BROWSER_ERROR, LOGIN_FAILED
from app.chat.turn.abort import finalize_inflight_turn_abort
from app.chat.turn.cancel_watch import watch_turn_cancel
from app.chat.turn.checkpoint import capture_turn_checkpoint_messages
from app.chat.turn.event_recorder import TurnAgentEventRecorder
from app.chat.turn.gate_emit import emit_user_gates
from app.chat.turn.gate_suspend import persist_gate_suspend_tool_results
from app.chat.turn.guards import TurnGuards
from app.chat.turn.lifecycle import clear_turn_active
from app.chat.turn.lock import conversation_turn_lock
from app.chat.turn.observation import TurnObservationContext
from app.chat.turn.observation_store import (
    clear_turn_observation_snapshot,
    load_turn_observation_snapshot,
)
from app.chat.turn.persistence import TurnPersistence
from app.chat.turn.trace import bind_turn_trace, log_stage
from app.chat.turn.usage_finalize import finalize_observation_segment
from app.chat.turn.usage_log import TerminatedBy
from app.chat.workspace import conversation_workspace
from app.chat.workspace.session import ensure_workspace_session
from app.core.checkpointer import get_chat_checkpointer
from app.core.config import settings
from app.core.logger import logger
from app.core.memory_store import get_memory_store
from app.core.turn.tool_loop_guard import TurnToolLoopGuard
from app.domain.chat.enums import StreamErrorCode, normalize_stream_error_code
from app.models.chat_conversations import ChatConversations

_STREAM_END = object()

_GATE_RESUME_FATAL_ERRORS = frozenset({BROWSER_ERROR, LOGIN_FAILED})


async def stream_chat_resume(
    *,
    conversation: ChatConversations,
    user_id: int,
    conversation_id: int,
    model_key: str,
    turn_id: str,
    gate_id: str,
    action: str,
    fields: dict[str, Any] | None,
    cancel_event: asyncio.Event,
    lock_held: bool = False,
) -> AsyncIterator[str]:
    """Resume LangGraph agent after user gate submit/cancel."""
    out: asyncio.Queue[str | None] = asyncio.Queue()

    async def emit(frame: StreamFrame) -> None:
        await out.put(encode_sse_frame(frame))

    async def agent_loop() -> None:
        gate_interrupted = False
        agent = None
        config = None
        turn_start_messages: list[BaseMessage] | None = None
        cancel_watch_task: asyncio.Task | None = None
        persistence = TurnPersistence(user_id=user_id, conversation_id=conversation_id)
        observation: TurnObservationContext | None = None
        recorder: TurnAgentEventRecorder | None = None
        guards: TurnGuards | None = None
        last_step_index = 0
        segment_started = time.monotonic()
        terminated_by: TerminatedBy = "completed"
        gate_resume_failed: dict[str, str] | None = None
        try:
            bind_turn_trace(turn_id=turn_id, conversation_id=conversation_id)
            cancel_watch_task = asyncio.create_task(
                watch_turn_cancel(
                    conversation_id=conversation_id,
                    turn_id=turn_id,
                    cancel_event=cancel_event,
                )
            )
            if not lock_held:
                await conversation_turn_lock.acquire(conversation_id, turn_id)
            snapshot = await load_turn_observation_snapshot(conversation_id, turn_id)
            if snapshot is None:
                raise RuntimeError("turn observation snapshot missing for gate resume")
            observation = TurnObservationContext.from_snapshot(
                user_id=user_id,
                conversation_id=conversation_id,
                turn_id=turn_id,
                gate_id=gate_id,
                resume_action=action,
                persistence=persistence,
                snapshot=snapshot,
            )
            recorder = TurnAgentEventRecorder(observation=observation)
            workspace = conversation_workspace(user_id, conversation_id)
            ensure_workspace_session(workspace)
            guards = TurnGuards(
                max_model_steps=settings.CHAT_MAX_ITERATIONS,
                max_tool_calls=settings.CHAT_MAX_TOOL_CALLS,
                wall_clock_sec=settings.CHAT_TURN_WALL_CLOCK_SEC,
                tool_repeat_guard=settings.CHAT_TOOL_REPEAT_GUARD,
            )
            loop_guard = TurnToolLoopGuard(surface="chat")
            ctx = ChatToolContext(
                user_id=user_id,
                conversation_id=conversation_id,
                workspace=workspace,
                audit=[],
                guards=guards,
                loop_guard=loop_guard,
                cancel_event=cancel_event,
            )
            tools = build_langchain_tools(ctx, enable_tools=True)
            tools.extend(load_mcp_tools())
            spec = get_model_spec(model_key)
            llm = GatewayChatModel(
                model_key=model_key,
                spec=spec,
                cancel_event=cancel_event,
            )
            agent = build_chat_agent(
                llm,
                tools,
                get_chat_checkpointer(),
                store=get_memory_store(),
            )
            config = chat_runnable_config(
                user_id=user_id,
                conversation_id=conversation_id,
                workspace=str(workspace),
                turn_id=turn_id,
            )
            turn_start_messages = await capture_turn_checkpoint_messages(agent, config)
            resume_payload: dict[str, Any] = {"action": action, "gate_id": gate_id}
            if fields is not None:
                resume_payload["fields"] = fields
            log_stage(
                "chat.turn.resume",
                conversation_id=conversation_id,
                turn_id=turn_id,
                gate_id=gate_id,
                action=action,
                stream_phase=observation.phase,
            )
            async for event in run_agent_turn_stream(
                agent,
                Command(resume=resume_payload),
                turn_id=turn_id,
                config=config,
                tools_by_name={tool.name: tool for tool in tools},
            ):
                if cancel_event.is_set():
                    terminated_by = "error"
                    break
                last_step_index = event.step_index
                for frame in frames_from_agent_event(event, turn_id=turn_id):
                    await emit(frame)
                if event.type in {
                    AgentEventType.TOOL_STARTED,
                    AgentEventType.MODEL_STEP_FINISHED,
                    AgentEventType.TOOL_FINISHED,
                }:
                    await recorder.record(event, ctx=ctx, guards=guards)
                if (
                    event.type == AgentEventType.TOOL_FINISHED
                    and event.tool_name == REQUEST_USER_GATE
                    and event.error_class in _GATE_RESUME_FATAL_ERRORS
                ):
                    gate_resume_failed = {
                        "error_class": event.error_class,
                        "message": event.tool_result.strip(),
                    }
                    log_stage(
                        "chat.turn.gate_resume_failed",
                        conversation_id=conversation_id,
                        turn_id=turn_id,
                        gate_id=gate_id,
                        error_class=gate_resume_failed["error_class"],
                        stream_phase=observation.phase,
                    )
                    break

            if gate_resume_failed is not None:
                terminated_by = "error"
                await persistence.persist_turn_error(
                    turn_id=turn_id,
                    step_index=last_step_index,
                    content=gate_resume_failed["message"],
                    error_code=gate_resume_failed["error_class"],
                )
                observation.usage_collector.note_termination(
                    terminated_by="error",
                    message=gate_resume_failed["message"],
                )
                await clear_gate_pending(conversation_id)
                await turn_auth_store.clear_turn_auth(conversation_id, turn_id)
                await clear_turn_active(conversation)
                await clear_turn_observation_snapshot(conversation_id, turn_id)
                await emit(
                    create_stream_frame(
                        type=StreamFrameType.ERROR,
                        protocol_version=settings.CHAT_SSE_PROTOCOL_VERSION,
                        code=StreamErrorCode.TURN_FAILED,
                        message=gate_resume_failed["message"],
                        turn_id=turn_id,
                    )
                )
                await emit(
                    create_stream_frame(
                        type=StreamFrameType.DONE,
                        protocol_version=settings.CHAT_SSE_PROTOCOL_VERSION,
                        turn_id=turn_id,
                        message_ids=persistence.message_ids,
                    )
                )
                return

            if cancel_event.is_set():
                terminated_by = "error"
                await finalize_inflight_turn_abort(
                    user_id=user_id,
                    conversation=conversation,
                    conversation_id=conversation_id,
                    turn_id=turn_id,
                    step_index=last_step_index,
                    persistence=persistence,
                    agent=agent,
                    config=config,
                    turn_start_messages=turn_start_messages,
                    already_persisted_close=recorder.final_persisted,
                )
                observation.usage_collector.note_termination(
                    terminated_by="error",
                    message="user cancelled",
                )
                await emit(
                    create_stream_frame(
                        type=StreamFrameType.CANCELLED,
                        protocol_version=settings.CHAT_SSE_PROTOCOL_VERSION,
                        turn_id=turn_id,
                        reason="user_cancel",
                    )
                )
                await emit(
                    create_stream_frame(
                        type=StreamFrameType.DONE,
                        protocol_version=settings.CHAT_SSE_PROTOCOL_VERSION,
                        turn_id=turn_id,
                        message_ids=persistence.message_ids,
                    )
                )
                return

            gate_interrupted = await emit_user_gates(
                agent,
                config,
                emit,
                turn_id=turn_id,
                conversation_id=conversation_id,
                model_key=model_key,
                workspace=workspace,
            )
            if gate_interrupted:
                await persist_gate_suspend_tool_results(
                    agent=agent,
                    config=config,
                    observation=observation,
                )
                terminated_by = "interrupted"
                observation.usage_collector.note_termination(
                    terminated_by="interrupted",
                    message=None,
                )
            else:
                pending = await get_gate_pending(conversation_id)
                cleared_gate_id = str(pending.get("gate_id") or "") if pending else ""
                await clear_gate_pending(conversation_id)
                if cleared_gate_id:
                    gate_assets.delete_gate_assets(workspace, cleared_gate_id)
                    await gate_meta_store.clear_gate_meta(cleared_gate_id)
                await turn_auth_store.clear_turn_auth(conversation_id, turn_id)
                await clear_turn_active(conversation)
                await clear_turn_observation_snapshot(conversation_id, turn_id)
                observation.usage_collector.note_termination(
                    terminated_by="completed",
                    message=None,
                )
            await emit(
                create_stream_frame(
                    type=StreamFrameType.DONE,
                    protocol_version=settings.CHAT_SSE_PROTOCOL_VERSION,
                    turn_id=turn_id,
                    message_ids=persistence.message_ids,
                )
            )
        except Exception as exc:
            logger.exception(
                "chat.turn.resume_failed",
                conversation_id=conversation_id,
                turn_id=turn_id,
                error=str(exc),
            )
            if observation is not None:
                observation.usage_collector.note_failure(exc)
                observation.usage_collector.note_termination(
                    terminated_by="error",
                    message="resume failed",
                )
                await persistence.persist_turn_error(
                    turn_id=turn_id,
                    step_index=last_step_index,
                    content="resume failed",
                    error_code="internal",
                )
            terminated_by = "error"
            await emit(
                create_stream_frame(
                    type=StreamFrameType.ERROR,
                    protocol_version=settings.CHAT_SSE_PROTOCOL_VERSION,
                    code=normalize_stream_error_code(None),
                    message="resume failed",
                    turn_id=turn_id,
                )
            )
            await emit(
                create_stream_frame(
                    type=StreamFrameType.DONE,
                    protocol_version=settings.CHAT_SSE_PROTOCOL_VERSION,
                    turn_id=turn_id,
                    message_ids=persistence.message_ids,
                )
            )
        finally:
            if cancel_watch_task is not None:
                cancel_watch_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await cancel_watch_task
            if observation is not None and guards is not None:
                try:
                    finalize_observation_segment(
                        observation,
                        model_steps_used=guards.model_steps_used,
                        tool_calls_used=guards.tool_calls_used,
                        wall_clock_seconds=time.monotonic() - segment_started,
                        terminated_by=terminated_by,
                        termination_message=observation.usage_collector.termination_message,
                    )
                except Exception:
                    logger.exception(
                        "chat.turn.resume.usage_record_failed",
                        conversation_id=conversation_id,
                        turn_id=turn_id,
                    )
            if not lock_held:
                await conversation_turn_lock.release(conversation_id, turn_id)
            await out.put(_STREAM_END)

    task = asyncio.create_task(agent_loop())
    try:
        while True:
            if cancel_event.is_set():
                yield encode_sse_frame(
                    create_stream_frame(
                        type=StreamFrameType.CANCELLED,
                        protocol_version=settings.CHAT_SSE_PROTOCOL_VERSION,
                        turn_id=turn_id,
                        reason="user_cancel",
                    )
                )
                break
            try:
                item = await asyncio.wait_for(out.get(), timeout=float(settings.CHAT_HEARTBEAT_INTERVAL_SEC))
            except asyncio.TimeoutError:
                yield encode_sse_frame(
                    create_stream_frame(
                        type=StreamFrameType.HEARTBEAT,
                        protocol_version=settings.CHAT_SSE_PROTOCOL_VERSION,
                        ts=int(time.time()),
                        turn_id=turn_id,
                    )
                )
                continue
            if item is _STREAM_END:
                break
            yield item
    finally:
        if not task.done():
            task.cancel()
