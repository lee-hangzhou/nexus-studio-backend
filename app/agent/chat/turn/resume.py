"""Resume chat turn after UserGate interrupt."""

from __future__ import annotations

import asyncio
import contextlib
import time
from typing import Any, AsyncIterator

from langchain_core.messages import BaseMessage
from langgraph.types import Command

from app.agent.chat.agent.factory import build_chat_agent
from app.agent.chat.llm.gateway_chat_model import GatewayChatModel
from app.agent.chat.llm.registry import get_model_spec
from app.agent.chat.mcp.client import load_mcp_tools
from app.agent.chat.memory.store import chat_runnable_config
from app.agent.chat.stream.encoder import encode_sse_frame
from app.agent.chat.stream.frames import StreamFrameType, create_stream_frame
from app.agent.chat.tools.lc_tools import ChatToolContext, build_langchain_tools
from app.agent.chat.turn.abort import finalize_inflight_turn_abort
from app.agent.chat.turn.cancel_watch import watch_turn_cancel
from app.agent.chat.turn.checkpoint import capture_turn_checkpoint_messages
from app.agent.chat.turn.event_recorder import TurnAgentEventRecorder
from app.agent.chat.turn.guards import TurnGuards
from app.agent.chat.turn.lock import conversation_turn_lock
from app.agent.chat.turn.observation import TurnObservationContext
from app.agent.chat.turn.observation_store import load_turn_observation_snapshot
from app.agent.chat.turn.persistence import TurnPersistence
from app.agent.chat.turn.subscribers import ChatResumeSubscriber
from app.agent.chat.turn.subscribers_resume import ChatResumeControlHook
from app.agent.chat.turn.trace import bind_turn_trace, log_stage
from app.agent.chat.turn.usage_finalize import finalize_observation_segment
from app.agent.chat.workspace import conversation_workspace
from app.agent.chat.workspace.session import ensure_workspace_session
from app.agent.runtime.checkpointer import get_chat_checkpointer
from app.agent.runtime.memory_store import get_memory_store
from app.agent.runtime.turn_engine.prepared import PreparedTurn
from app.agent.runtime.turn.tool_loop_guard import TurnToolLoopGuard
from app.agent.runtime.turn_engine.entry import stream_prepared_turn
from app.agent.runtime.turn_engine.terminal_policy import SseTerminalPolicy
from app.server.chat.domain.stream_enums import normalize_stream_error_code
from app.server.chat.persistence.conversations import ChatConversations
from app.server.chat.services.constants import CHAT_CHECKPOINT_THREAD_PREFIX
from app.server.infra.config import settings
from app.server.infra.logger import logger


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
    cancel_watch_task: asyncio.Task | None = None
    persistence = TurnPersistence(user_id=user_id, conversation_id=conversation_id)
    observation: TurnObservationContext | None = None
    recorder: TurnAgentEventRecorder | None = None
    guards: TurnGuards | None = None
    resume_sub: ChatResumeSubscriber | None = None
    agent = None
    config = None
    turn_start_messages: list[BaseMessage] | None = None
    segment_started = time.monotonic()
    terminated_by = "completed"
    last_step_index = 0
    acquired = False

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
            acquired = True

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
        thread_id = f"{CHAT_CHECKPOINT_THREAD_PREFIX}-{conversation_id}"
        control_hook = ChatResumeControlHook()
        resume_sub = ChatResumeSubscriber(
            persistence=persistence,
            recorder=recorder,
            observation=observation,
            guards=guards,
            ctx=ctx,
            turn_id=turn_id,
            conversation_id=conversation_id,
            model_key=model_key,
            workspace=workspace,
            cancel_event=cancel_event,
            conversation=conversation,
            agent=agent,
            runnable_config=config,
            control_hook=control_hook,
        )

        prepared = PreparedTurn(
            agent=agent,
            turn_id=turn_id,
            conversation_id=conversation_id,
            user_id=user_id,
            resume_command=Command(resume=resume_payload),
            runnable_config=config,
            thread_id=thread_id,
            guards=guards,
            cancel_event=cancel_event,
            subscribers=[resume_sub],
            heartbeat_interval_sec=int(settings.CHAT_HEARTBEAT_INTERVAL_SEC),
            is_resume=True,
            recovery_hook=control_hook,
            terminal_policy=SseTerminalPolicy(
                emit_done_on_completed=True,
                emit_done_after_failure=True,
                message_ids=lambda: persistence.message_ids,
            ),
        )
        async for chunk in stream_prepared_turn(prepared):
            yield chunk

        terminated_by = resume_sub.terminated_by
        last_step_index = resume_sub.last_step_index

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
            # CANCELLED/DONE already emitted by turn engine / sse_pump during the stream.
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
        # Orphan path: turn engine did not run or failed before SSE ownership attached.
        yield encode_sse_frame(
            create_stream_frame(
                type=StreamFrameType.ERROR,
                protocol_version=settings.CHAT_SSE_PROTOCOL_VERSION,
                code=normalize_stream_error_code(None),
                message="resume failed",
                turn_id=turn_id,
            )
        )
        yield encode_sse_frame(
            create_stream_frame(
                type=StreamFrameType.DONE,
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
                if resume_sub is not None:
                    terminated_by = resume_sub.terminated_by
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
        if acquired:
            await conversation_turn_lock.release(conversation_id, turn_id)
