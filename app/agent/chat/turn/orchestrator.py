"""Chat turn SSE: checkpointer-backed agent loop."""

from __future__ import annotations

import asyncio
import contextlib
import time
from typing import Any, AsyncIterator

from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.runnables import RunnableConfig

from app.agent.chat.agent.events import AgentEventType
from app.agent.chat.agent.factory import build_chat_agent
from app.agent.chat.agent.runner import run_agent_turn_stream
from app.server.chat.services.attachments.service import chat_attachment_service
from app.server.chat.services.attachments.status import attachment_status_label
from app.server.chat.services.attachments.turn_prep import apply_attachment_intent
from app.agent.chat.conversation_title import (
    generate_conversation_title_via_llm,
    is_first_user_message,
    is_placeholder_title,
)
from app.agent.chat.llm.gateway_chat_model import GatewayChatModel
from app.agent.chat.llm.registry import get_model_spec
from app.agent.chat.mcp.client import load_mcp_tools
from app.agent.chat.memory.store import chat_runnable_config
from app.agent.chat.memory.turn_input import build_turn_human_message
from app.agent.chat.prompt.composer import PromptComposer
from app.agent.chat.prompt.types import AttachmentBrief, TurnPromptContext
from app.agent.chat.stream.agent_frames import chunk_text, frames_from_agent_event
from app.agent.chat.stream.encoder import encode_sse_frame
from app.agent.chat.stream.frames import StreamFrame, StreamFrameType, create_stream_frame
from app.agent.chat.tools.result import BROWSER_BLOCKED, ToolResult as _ToolResult
from app.agent.chat.tools.lc_tools import ChatToolContext, build_langchain_tools
from app.agent.runtime.turn.tool_loop_guard import TurnToolLoopGuard
from app.agent.chat.turn.context import build_turn_context
from app.agent.chat.turn.empty_recovery import recover_empty_answer
from app.agent.chat.turn.gate_emit import emit_user_gates, has_pending_user_gate
from app.agent.chat.gate.pending import clear_gate_pending
from app.agent.chat.gate import turn_auth as turn_auth_store
from app.agent.chat.turn.guards import TurnGuards
from app.agent.chat.turn.lifecycle import clear_turn_active, mark_turn_active, touch_conversation_updated
from app.agent.chat.turn.abort import finalize_inflight_turn_abort
from app.agent.chat.turn.cancel_watch import watch_turn_cancel
from app.agent.chat.turn.checkpoint import capture_turn_checkpoint_messages
from app.agent.chat.turn.lock import conversation_turn_lock
from app.agent.chat.turn.persistence import (
    TurnPersistence,
    finalize_assistant,
    finalize_published_deliverables,
    persist_user_message,
)
from app.agent.chat.turn.event_recorder import TurnAgentEventRecorder
from app.agent.chat.turn.gate_suspend import persist_gate_suspend_tool_results
from app.agent.chat.turn.observation import TurnObservationContext
from app.agent.chat.turn.observation_store import (
    clear_turn_observation_snapshot,
    save_turn_observation_snapshot,
)
from app.agent.chat.turn.trace import bind_turn_trace, log_stage
from app.agent.chat.turn.usage_log import TerminatedBy, TurnUsageCollector
from app.agent.chat.turn.usage_finalize import finalize_observation_segment
from app.agent.chat.vision.gate import assert_vision_turn_allowed, is_image_mime
from app.agent.chat.workspace import conversation_workspace
from app.agent.chat.workspace.session import ensure_workspace_session
from app.contracts.metadata import (
    AssistantMessageMetadata,
    ToolAuditMetadata,
    ToolRecoveryMetadata,
    TurnContextMetadata,
)
from app.agent.runtime.checkpointer import get_chat_checkpointer
from app.agent.runtime.memory_store import get_memory_store
from app.server.infra.config import settings
from app.server.infra.logger import log_exception, logger
from app.server.chat.domain.stream_enums import StreamErrorCode, normalize_stream_error_code
from app.server.chat.persistence.attachments import ChatAttachments
from app.server.chat.persistence.conversations import ChatConversations

_STREAM_END: None = None


async def _background_conversation_title(
    *,
    user_id: int,
    conversation_id: int,
    content: str,
    attachment_ids: list[int],
    model_key: str,
) -> None:
    """首条用户消息后异步生成会话标题，不阻塞 SSE DONE 与 turn 锁释放。"""
    try:
        await generate_conversation_title_via_llm(
            user_id=user_id,
            conversation_id=conversation_id,
            user_content=content,
            attachment_ids=attachment_ids,
            model_key=model_key,
        )
    except Exception:
        logger.exception(
            "chat.conversation_title.background_failed",
            conversation_id=conversation_id,
        )


async def stream_turn(
    *,
    conversation: ChatConversations,
    user_id: int,
    conversation_id: int,
    content: str,
    model_key: str,
    attachment_ids: list[int],
    enable_tools: bool,
    client_turn_id: str | None,
    cancel_event: asyncio.Event,
    turn_id: str,
) -> AsyncIterator[str]:
    spec = get_model_spec(model_key)
    out: asyncio.Queue[str | None] = asyncio.Queue()
    last_activity = time.monotonic()
    agent_done = False

    def touch() -> None:
        nonlocal last_activity
        last_activity = time.monotonic()

    async def emit(frame: StreamFrame) -> None:
        await out.put(encode_sse_frame(frame))

    async def agent_loop() -> None:
        nonlocal last_activity, agent_done
        persistence = TurnPersistence(user_id=user_id, conversation_id=conversation_id)
        guards: TurnGuards | None = None
        usage_collector: TurnUsageCollector | None = None
        observation: TurnObservationContext | None = None
        recorder: TurnAgentEventRecorder | None = None
        terminated_by: TerminatedBy = "error"
        recovery_exhausted = False
        recovery_used = False
        force_recovery_messages: list[Any] | None = None
        gate_interrupted = False
        agent = None
        streamed_answer_parts_by_step: dict[int, list[str]] = {}
        last_step_index = 0
        config: RunnableConfig | None = None
        turn_start_messages: list[BaseMessage] | None = None
        cancel_watch_task: asyncio.Task | None = None
        try:
            await mark_turn_active(conversation, turn_id)
            cancel_watch_task = asyncio.create_task(
                watch_turn_cancel(
                    conversation_id=conversation_id,
                    turn_id=turn_id,
                    cancel_event=cancel_event,
                )
            )

            bind_turn_trace(turn_id=turn_id, conversation_id=conversation_id)
            await apply_attachment_intent(user_id, conversation_id, attachment_ids)
            attachment_rows = await ChatAttachments.filter(
                user_id=user_id,
                conversation_id=conversation_id,
                is_attached=True,
            )
            turn_ctx = build_turn_context(
                has_attachments=bool(attachment_rows),
                enable_tools=enable_tools,
            )
            log_stage(
                "turn.context",
                has_attachments=turn_ctx.has_attachments,
                enable_tools=turn_ctx.enable_tools,
            )

            turn_context_meta = TurnContextMetadata(
                has_attachments=turn_ctx.has_attachments,
                enable_tools=turn_ctx.enable_tools,
            )

            workspace = conversation_workspace(user_id, conversation_id)
            ensure_workspace_session(workspace)
            materialized: list = []
            materialized_by_id: dict = {}
            if turn_ctx.enable_materialize:
                mat_started = time.perf_counter()
                materialized = await chat_attachment_service.materialize_to_workspace(
                    workspace,
                    list(attachment_rows),
                )
                materialized_by_id = {item.attachment_id: item for item in materialized}
                log_stage("turn.materialize", started=mat_started, count=len(materialized))

            guards = TurnGuards(
                max_model_steps=settings.CHAT_MAX_ITERATIONS,
                max_tool_calls=settings.CHAT_MAX_TOOL_CALLS,
                wall_clock_sec=settings.CHAT_TURN_WALL_CLOCK_SEC,
                tool_repeat_guard=settings.CHAT_TOOL_REPEAT_GUARD,
            )
            tool_audit: list[ToolAuditMetadata] = []
            usage_collector = TurnUsageCollector(
                conversation_id=conversation_id,
                turn_id=turn_id,
                model_key=model_key,
                limits={
                    "max_iterations": settings.CHAT_MAX_ITERATIONS,
                    "max_tool_calls": settings.CHAT_MAX_TOOL_CALLS,
                    "wall_clock_sec": settings.CHAT_TURN_WALL_CLOCK_SEC,
                },
                attachments=[item.workspace_path for item in materialized],
            )
            observation = TurnObservationContext.for_main_turn(
                user_id=user_id,
                conversation_id=conversation_id,
                turn_id=turn_id,
                model_key=model_key,
                persistence=persistence,
                usage_collector=usage_collector,
                turn_context=turn_context_meta,
                tool_audit=tool_audit,
            )
            recorder = TurnAgentEventRecorder(observation=observation)
            await save_turn_observation_snapshot(
                conversation_id=conversation_id,
                turn_id=turn_id,
                model_key=model_key,
                turn_context=turn_context_meta,
                limits=usage_collector.limits,
                attachments=usage_collector.attachments,
            )
            loop_guard = TurnToolLoopGuard(surface="chat")
            ctx = ChatToolContext(
                user_id=user_id,
                conversation_id=conversation_id,
                workspace=workspace,
                audit=tool_audit,
                guards=guards,
                cancel_event=cancel_event,
                loop_guard=loop_guard,
            )
            plan_enable_tools = enable_tools and turn_ctx.enable_tools
            tools = build_langchain_tools(ctx, enable_tools=plan_enable_tools)
            tools = tools + load_mcp_tools()
            attachment_briefs = [
                AttachmentBrief(
                    attachment_id=row.id,
                    filename=row.filename,
                    mime_type=row.mime_type or "application/octet-stream",
                    status=attachment_status_label(row.status),
                    is_attached=bool(row.is_attached),
                    workspace_path=(mat.workspace_path if (mat := materialized_by_id.get(row.id)) else ""),
                )
                for row in attachment_rows
            ]
            workspace_hint = f"会话工作区相对路径根目录：{workspace}"
            requested_ids = set(attachment_ids)
            image_attachment_ids = [
                row.id
                for row in attachment_rows
                if row.id in requested_ids and is_image_mime(row.mime_type or "")
            ]
            assert_vision_turn_allowed(spec=spec, image_attachment_ids=image_attachment_ids)
            prompt_ctx = TurnPromptContext(
                user_id=user_id,
                conversation_id=conversation_id,
                model_key=model_key,
                enable_tools=plan_enable_tools,
                tool_names=[tool.name for tool in tools],
                has_vision_images=bool(image_attachment_ids),
            )
            system_prompt = PromptComposer.build_turn_system(prompt_ctx)

            turn_human = build_turn_human_message(
                content,
                attachments=attachment_briefs,
                workspace_hint=workspace_hint,
                image_attachment_ids=image_attachment_ids,
            )

            await persist_user_message(
                user_id=user_id,
                conversation_id=conversation_id,
                content=content,
                turn_human=turn_human,
                attachment_ids=attachment_ids,
                turn_id=turn_id,
                client_turn_id=client_turn_id,
            )

            attachment_binary_paths = {
                item.attachment_id: item.workspace_path
                for item in materialized
                if item.attachment_id in image_attachment_ids
            }
            llm = GatewayChatModel(
                model_key=model_key,
                spec=spec,
                cancel_event=cancel_event,
                vision_hydrate_attachment_ids=image_attachment_ids,
                vision_attachment_binary_paths=attachment_binary_paths,
                vision_conversation_workspace=str(workspace),
            )
            checkpointer = get_chat_checkpointer()
            agent = build_chat_agent(
                llm,
                tools,
                checkpointer,
                system_prompt=system_prompt,
                store=get_memory_store(),
            )
            config = chat_runnable_config(
                user_id=user_id,
                conversation_id=conversation_id,
                workspace=str(workspace),
                turn_id=turn_id,
            )
            turn_start_messages = await capture_turn_checkpoint_messages(agent, config)

            async def attempt_final_recovery(
                messages: list[Any],
                *,
                reason: str,
            ) -> bool:
                nonlocal recovery_exhausted, recovery_used, terminated_by
                recovery_used = True
                remaining_steps = max(0, guards.max_model_steps - guards.model_steps_used)
                max_attempts = min(settings.CHAT_EMPTY_RECOVERY_ATTEMPTS, remaining_steps)
                if cancel_event.is_set() or not guards.check_wall_clock():
                    max_attempts = 0
                recovery_timeout = max(
                    1,
                    min(
                        settings.CHAT_EMPTY_RECOVERY_TIMEOUT_SEC,
                        int(guards.remaining_wall_clock_seconds()),
                    ),
                )
                log_stage(
                    "empty_recovery.started",
                    model=model_key,
                    reason=reason,
                    max_attempts=max_attempts,
                    model_steps=guards.model_steps_used,
                )
                result = await recover_empty_answer(
                    llm,
                    messages,
                    config=config,
                    system_prompt=system_prompt,
                    max_attempts=max_attempts,
                    timeout_sec=recovery_timeout,
                    cancel_event=cancel_event,
                )
                for offset in range(result.attempts):
                    guards.on_model_step_finished()
                    usage_collector.note_model_step(
                        step_index=last_step_index + offset + 1,
                        tool_calls=[],
                        content_len=len(result.text or "") if offset == result.attempts - 1 else 0,
                    )
                usage_collector.note_empty_recovery(
                    attempts=result.attempts,
                    succeeded=bool(result.text),
                    failure_reason=result.failure_reason,
                )
                if result.failure_reason == "cancelled" or cancel_event.is_set():
                    await persistence.persist_cancelled_close(
                        turn_id=turn_id,
                        step_index=last_step_index,
                    )
                    recorder.final_persisted = True
                    terminated_by = "error"
                    usage_collector.note_termination(
                        terminated_by="error",
                        message="user cancelled during recovery",
                    )
                    await emit(
                        create_stream_frame(
                            type=StreamFrameType.CANCELLED,
                            turn_id=turn_id,
                            reason="user_cancelled",
                        )
                    )
                    return False
                if result.text:
                    log_stage(
                        "empty_recovery.succeeded",
                        model=model_key,
                        reason=reason,
                        attempts=result.attempts,
                        model_steps=guards.model_steps_used,
                        duration_ms=result.duration_ms,
                        answer_chars=len(result.text),
                    )
                    for piece in chunk_text(result.text):
                        streamed_answer_parts_by_step.setdefault(last_step_index + 1, []).append(piece)
                        await emit(
                            create_stream_frame(
                                type=StreamFrameType.TOKEN,
                                protocol_version=settings.CHAT_SSE_PROTOCOL_VERSION,
                                channel="answer",
                                text=piece,
                            )
                        )
                    await finalize_assistant(
                        persistence=persistence,
                        ctx=ctx,
                        turn_id=turn_id,
                        content=result.text,
                        ai_message=AIMessage(content=result.text),
                        metadata=AssistantMessageMetadata(
                            turn_context=turn_context_meta,
                            tool_steps=recorder.tool_steps,
                            tool_audit=tool_audit if settings.CHAT_PERSIST_TOOL_AUDIT else [],
                            recovery=ToolRecoveryMetadata(
                                recovered=True,
                                recovery_type="empty_response",
                                recovery_reason=reason,
                                recovery_attempts=result.attempts,
                            ),
                        ),
                        user_id=user_id,
                        conversation_id=conversation_id,
                    )
                    recorder.final_persisted = True
                    terminated_by = "completed"
                    usage_collector.note_termination(terminated_by="completed", message=None)
                    return True

                log_stage(
                    "empty_recovery.failed",
                    model=model_key,
                    reason=reason,
                    attempts=result.attempts,
                    model_steps=guards.model_steps_used,
                    failure_reason=result.failure_reason,
                    duration_ms=result.duration_ms,
                )
                friendly_message = "本次回答生成失败，请重试"
                await finalize_assistant(
                    persistence=persistence,
                    ctx=ctx,
                    turn_id=turn_id,
                    content=friendly_message,
                    ai_message=AIMessage(content=friendly_message),
                    metadata=AssistantMessageMetadata(
                        turn_context=turn_context_meta,
                        tool_steps=recorder.tool_steps,
                        tool_audit=tool_audit if settings.CHAT_PERSIST_TOOL_AUDIT else [],
                        recovery=ToolRecoveryMetadata(
                            recovered=True,
                            recovery_type="empty_response",
                            recovery_reason=reason,
                            recovery_attempts=result.attempts,
                            recovery_failure_reason=result.failure_reason,
                            recovery_exhausted=True,
                        ),
                    ),
                    user_id=user_id,
                    conversation_id=conversation_id,
                )
                recorder.final_persisted = True
                recovery_exhausted = True
                terminated_by = "error"
                usage_collector.note_termination(
                    terminated_by="error",
                    message="agent recovery exhausted",
                )
                await emit(
                    create_stream_frame(
                        type=StreamFrameType.ERROR,
                        protocol_version=settings.CHAT_SSE_PROTOCOL_VERSION,
                        code="agent_recovery_exhausted",
                        message=friendly_message,
                    )
                )
                return False

            turn_emitted_done = False
            async for event in run_agent_turn_stream(
                agent,
                [turn_human],
                turn_id=turn_id,
                config=config,
            ):
                if cancel_event.is_set():
                    terminated_by = "error"
                    if not recorder.final_persisted:
                        await persistence.persist_cancelled_close(
                            turn_id=turn_id,
                            step_index=last_step_index,
                        )
                        recorder.final_persisted = True
                    usage_collector.note_termination(
                        terminated_by="error",
                        message="user cancelled",
                    )
                    await emit(
                        create_stream_frame(
                            type=StreamFrameType.CANCELLED,
                            turn_id=turn_id,
                            reason="user_cancel",
                        )
                    )
                    break
                if not guards.check_wall_clock():
                    logger.warning(
                        "chat.stream_turn.wall_clock_exceeded",
                        conversation_id=conversation_id,
                        turn_id=turn_id,
                        wall_clock_sec=guards.wall_clock_seconds(),
                    )
                    cancel_event.set()
                    await persistence.persist_cancelled_close(
                        turn_id=turn_id,
                        step_index=last_step_index,
                    )
                    timeout_message = "本轮执行超时，已终止"
                    usage_collector.note_termination(terminated_by="wall_clock", message=timeout_message)
                    await emit(
                        create_stream_frame(
                            type=StreamFrameType.ERROR,
                            protocol_version=settings.CHAT_SSE_PROTOCOL_VERSION,
                            code="wall_clock",
                            message=timeout_message,
                        )
                    )
                    turn_emitted_done = True
                    terminated_by = "wall_clock"
                    break
                touch()
                if event.type == AgentEventType.MODEL_TOKEN and event.channel == "answer" and event.text:
                    streamed_answer_parts_by_step.setdefault(event.step_index, []).append(event.text)
                for frame in frames_from_agent_event(event, turn_id=turn_id):
                    await emit(frame)

                if event.type == AgentEventType.TOOL_STARTED:
                    await recorder.record(event)

                if event.type == AgentEventType.MODEL_STEP_FINISHED and event.ai_message is not None:
                    last_step_index = event.step_index
                    if guards is not None:
                        guards.on_model_step_finished()
                    if guards.exceeded_model_steps():
                        logger.warning(
                            "chat.stream_turn.max_model_steps",
                            conversation_id=conversation_id,
                            turn_id=turn_id,
                            step_index=event.step_index,
                            max_model_steps=guards.max_model_steps,
                        )
                        cancel_event.set()
                        await persistence.persist_cancelled_close(
                            turn_id=turn_id,
                            step_index=event.step_index,
                        )
                        max_steps_message = (
                            f"模型步数已达上限（{guards.max_model_steps} 步），"
                            "任务未完成，请简化需求或拆分后重试"
                        )
                        usage_collector.note_termination(terminated_by="max_steps", message=max_steps_message)
                        await emit(
                            create_stream_frame(
                                type=StreamFrameType.ERROR,
                                protocol_version=settings.CHAT_SSE_PROTOCOL_VERSION,
                                code="max_iterations",
                                message=max_steps_message,
                            )
                        )
                        turn_emitted_done = True
                        terminated_by = "max_steps"
                        break

                    answer_text = str(event.ai_message.content or "").strip()
                    step_answer_parts = streamed_answer_parts_by_step.get(event.step_index, [])
                    if answer_text and not event.ai_message.tool_calls and not step_answer_parts:
                        for piece in chunk_text(answer_text):
                            streamed_answer_parts_by_step.setdefault(event.step_index, []).append(piece)
                            await emit(
                                create_stream_frame(
                                    type=StreamFrameType.TOKEN,
                                    protocol_version=settings.CHAT_SSE_PROTOCOL_VERSION,
                                    channel="answer",
                                    text=piece,
                                )
                            )
                    await recorder.record(event, ctx=ctx)

                if event.type == AgentEventType.TOOL_FINISHED and event.tool_result is not None:
                    guard_signal = await recorder.record(event, guards=guards)
                    if guard_signal == "browser_blocked":
                        _blocked_model_text = ""
                        if recorder.last_model_message is not None:
                            _blocked_model_text = str(recorder.last_model_message.content or "").strip()
                        await emit(
                            create_stream_frame(
                                type=StreamFrameType.BROWSER_BLOCKED,
                                turn_id=turn_id,
                                message=_blocked_model_text,
                                screenshot_url=None,
                                conversation_id=conversation_id,
                            )
                        )
                        if _blocked_model_text and not recorder.final_persisted:
                            await finalize_assistant(
                                persistence=persistence,
                                ctx=ctx,
                                turn_id=turn_id,
                                content=_blocked_model_text,
                                ai_message=recorder.last_model_message,
                                metadata=AssistantMessageMetadata(
                                    turn_context=turn_context_meta,
                                    tool_steps=recorder.tool_steps,
                                    tool_audit=tool_audit if settings.CHAT_PERSIST_TOOL_AUDIT else [],
                                ),
                                user_id=user_id,
                                conversation_id=conversation_id,
                            )
                            recorder.final_persisted = True
                        terminated_by = "completed"
                        usage_collector.note_termination(terminated_by="completed", message=None)
                        log_stage(
                            "browser_blocked.exit",
                            tool_name=event.tool_name,
                            has_screenshot=False,
                            model_text_len=len(_blocked_model_text),
                            step_index=event.step_index,
                        )
                        await emit(
                            create_stream_frame(
                                type=StreamFrameType.DONE,
                                turn_id=turn_id,
                                message_ids=persistence.message_ids,
                            )
                        )
                        turn_emitted_done = True
                        break
                    if guard_signal == "stop_turn":
                        stop_reason = guards.last_stop_reason or "error"
                        state = await agent.aget_state(config)
                        force_recovery_messages = list(state.values.get("messages") or [])
                        log_stage(
                            "tool_recovery.exhausted",
                            model=model_key,
                            tool_name=event.tool_name,
                            error_code=event.error_class,
                            stop_reason=stop_reason,
                            step_index=event.step_index,
                            model_steps=guards.model_steps_used,
                        )
                        break

                if event.type == AgentEventType.TURN_COMPLETED:
                    last_step_index = max(last_step_index, event.step_index)
                    turn_messages = list(event.messages) if event.messages is not None else []
                    deliverable_meta = AssistantMessageMetadata(
                        turn_context=turn_context_meta,
                        tool_steps=recorder.tool_steps,
                        tool_audit=tool_audit if settings.CHAT_PERSIST_TOOL_AUDIT else [],
                        recovery=ToolRecoveryMetadata(
                            recovered=usage_collector.tool_recovery_count > 0,
                            recovery_type=(
                                "tool_call"
                                if usage_collector.tool_recovery_count > 0
                                else None
                            ),
                        ),
                    )
                    if not recorder.final_persisted:
                        msg_id = await finalize_published_deliverables(
                            persistence=persistence,
                            ctx=ctx,
                            turn_id=turn_id,
                            metadata=deliverable_meta,
                            user_id=user_id,
                            conversation_id=conversation_id,
                        )
                        if msg_id is not None:
                            recorder.final_persisted = True
                    if (
                        not recorder.final_persisted
                        and recorder.last_model_message is not None
                        and not recorder.last_model_message.tool_calls
                    ):
                        text = str(recorder.last_model_message.content or "").strip()
                        if text:
                            await finalize_assistant(
                                persistence=persistence,
                                ctx=ctx,
                                turn_id=turn_id,
                                content=text,
                                ai_message=recorder.last_model_message,
                                metadata=deliverable_meta,
                                user_id=user_id,
                                conversation_id=conversation_id,
                            )
                            recorder.final_persisted = True
                    if not recorder.final_persisted and agent is not None and await has_pending_user_gate(
                        agent, config
                    ):
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
                            usage_collector.note_termination(
                                terminated_by="interrupted",
                                message=None,
                            )
                    elif not recorder.final_persisted:
                        logger.warning(
                            "chat.stream_turn.empty_response",
                            conversation_id=conversation_id,
                            turn_id=turn_id,
                            last_step_index=last_step_index,
                            had_streamed_tokens=any(streamed_answer_parts_by_step.values()),
                            answer_chars=sum(
                                len(part)
                                for parts in streamed_answer_parts_by_step.values()
                                for part in parts
                            ),
                            think_chars=0,
                            tool_steps_count=len(recorder.tool_steps),
                            message_count=len(turn_messages) if turn_messages else 1,
                            turn_context=turn_context_meta.model_dump(mode="json"),
                        )
                        if recorder.tool_steps:
                            friendly_message = "模型服务暂时无响应，请重试"
                            recovery_exhausted = True
                            terminated_by = "error"
                            usage_collector.note_termination(
                                terminated_by="error",
                                message="gateway empty after tools",
                            )
                            await emit(
                                create_stream_frame(
                                    type=StreamFrameType.ERROR,
                                    protocol_version=settings.CHAT_SSE_PROTOCOL_VERSION,
                                    code=StreamErrorCode.GATEWAY_UPSTREAM_FAILED,
                                    message=friendly_message,
                                )
                            )
                        else:
                            await attempt_final_recovery(
                                turn_messages,
                                reason="empty_response",
                            )
                    if not recorder.final_persisted and not guards.check_wall_clock():
                        await persistence.persist_cancelled_close(turn_id=turn_id, step_index=event.step_index)
                        terminated_by = "wall_clock"
                        usage_collector.note_termination(
                            terminated_by="wall_clock",
                            message="本轮执行超时，已终止",
                        )
                    elif not recorder.final_persisted and not guards.check_model_steps():
                        await persistence.persist_cancelled_close(turn_id=turn_id, step_index=event.step_index)
                        terminated_by = "max_steps"
                    elif not gate_interrupted and not recovery_exhausted:
                        terminated_by = "completed"
                        usage_collector.note_termination(terminated_by="completed", message=None)

                    schedule_title = (
                        terminated_by == "completed"
                        and recorder.final_persisted
                        and not recovery_used
                        and await is_first_user_message(conversation_id)
                        and is_placeholder_title(conversation.title)
                    )

                    await emit(
                        create_stream_frame(
                            type=StreamFrameType.DONE,
                            turn_id=turn_id,
                            message_ids=persistence.message_ids,
                        )
                    )
                    logger.info(
                        "chat.stream_turn.completed",
                        conversation_id=conversation_id,
                        turn_id=turn_id,
                        final_persisted=recorder.final_persisted,
                        message_ids=persistence.message_ids,
                        terminated_by=terminated_by,
                    )
                    if schedule_title:
                        asyncio.create_task(
                            _background_conversation_title(
                                user_id=user_id,
                                conversation_id=conversation_id,
                                content=content,
                                attachment_ids=attachment_ids,
                                model_key=model_key,
                            )
                        )
                    turn_emitted_done = True
                    break

                if event.type == AgentEventType.TURN_FAILED:
                    terminated_by = "error"
                    gateway_error = event.error_class in {
                        "gateway_empty_stream",
                        "gateway_upstream_timeout",
                        "gateway_upstream_failed",
                    }
                    fail_message = (
                        "模型服务暂时无响应，请重试"
                        if gateway_error
                        else event.error
                    )
                    await persistence.persist_turn_error(
                        turn_id=turn_id,
                        step_index=event.step_index,
                        content=fail_message,
                        error_code=event.error_class,
                    )
                    usage_collector.note_failure(message=fail_message)
                    usage_collector.note_termination(terminated_by="error", message=fail_message)
                    logger.error(
                        "chat.stream_turn.agent_failed",
                        conversation_id=conversation_id,
                        turn_id=turn_id,
                        step_index=event.step_index,
                        error=fail_message,
                        error_class=event.error_class,
                    )
                    if not cancel_event.is_set():
                        await emit(
                            create_stream_frame(
                                type=StreamFrameType.ERROR,
                                protocol_version=settings.CHAT_SSE_PROTOCOL_VERSION,
                                code=normalize_stream_error_code(
                                    event.error_class,
                                    fallback=StreamErrorCode.TURN_FAILED,
                                ),
                                message=fail_message,
                            )
                        )
                    if cancel_event.is_set():
                        await emit(
                            create_stream_frame(
                                type=StreamFrameType.CANCELLED,
                                turn_id=turn_id,
                                reason=event.error_class,
                            )
                        )
                    turn_emitted_done = True
                    break

            if force_recovery_messages is not None and not turn_emitted_done and not cancel_event.is_set():
                recovery_succeeded = await attempt_final_recovery(
                    force_recovery_messages,
                    reason="tool_recovery_exhausted",
                )
                if recovery_succeeded:
                    await emit(
                        create_stream_frame(
                            type=StreamFrameType.DONE,
                            turn_id=turn_id,
                            message_ids=persistence.message_ids,
                        )
                    )
                turn_emitted_done = True

            if agent is not None and not gate_interrupted and not cancel_event.is_set():
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
                    usage_collector.note_termination(
                        terminated_by="interrupted",
                        message=None,
                    )
                    turn_emitted_done = True

            if not turn_emitted_done and not gate_interrupted:
                await emit(
                    create_stream_frame(
                        type=StreamFrameType.DONE,
                        turn_id=turn_id,
                        message_ids=persistence.message_ids,
                    )
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log_exception(
                "chat.stream_turn.failed",
                exc=exc,
                conversation_id=conversation_id,
                turn_id=turn_id,
            )
            if usage_collector is not None and not turn_emitted_done:
                usage_collector.note_failure(exc)
                usage_collector.note_termination(
                    terminated_by="error",
                    message=str(exc) or "turn failed",
                )
            if not turn_emitted_done:
                await emit(
                    create_stream_frame(
                        type=StreamFrameType.ERROR,
                        code="internal",
                        message="turn failed",
                    )
                )
                turn_emitted_done = True
        finally:
            if cancel_watch_task is not None:
                cancel_watch_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await cancel_watch_task
            if cancel_event.is_set():
                with contextlib.suppress(Exception):
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
            elif not gate_interrupted:
                try:
                    await turn_auth_store.clear_turn_auth(conversation_id, turn_id)
                    await clear_turn_active(conversation)
                    await clear_turn_observation_snapshot(conversation_id, turn_id)
                except Exception:
                    logger.exception(
                        "chat.stream_turn.clear_active_failed",
                        conversation_id=conversation_id,
                        turn_id=turn_id,
                    )
            # 收尾各步骤相互独立兜底，任何一步失败都不能跳过锁释放与 _STREAM_END，
            # 否则会话会被永久锁成 busy，或外层生成器永远等不到结束信号而挂起。
            try:
                await touch_conversation_updated(conversation)
            except Exception:
                logger.exception(
                    "chat.stream_turn.touch_failed",
                    conversation_id=conversation_id,
                    turn_id=turn_id,
                )
            try:
                if guards is not None and usage_collector is not None and observation is not None:
                    if usage_collector.terminated_by != terminated_by:
                        usage_collector.terminated_by = terminated_by
                    finalize_observation_segment(
                        observation,
                        model_steps_used=guards.model_steps_used,
                        tool_calls_used=guards.tool_calls_used,
                        wall_clock_seconds=guards.wall_clock_seconds(),
                        terminated_by=terminated_by,
                        termination_message=usage_collector.termination_message,
                    )
            except Exception:
                logger.exception(
                    "chat.stream_turn.usage_record_failed",
                    conversation_id=conversation_id,
                    turn_id=turn_id,
                )
            agent_done = True
            try:
                await conversation_turn_lock.release(conversation_id, turn_id)
            except Exception:
                logger.exception(
                    "chat.stream_turn.release_failed",
                    conversation_id=conversation_id,
                    turn_id=turn_id,
                )
            await out.put(_STREAM_END)

    async def heartbeat_loop() -> None:
        while True:
            await asyncio.sleep(settings.CHAT_HEARTBEAT_INTERVAL_SEC)
            if cancel_event.is_set():
                return
            await out.put(
                encode_sse_frame(
                    create_stream_frame(
                        type=StreamFrameType.HEARTBEAT,
                        protocol_version=settings.CHAT_SSE_PROTOCOL_VERSION,
                        ts=int(time.time()),
                    )
                )
            )

    agent_task = asyncio.create_task(agent_loop())
    hb_task = asyncio.create_task(heartbeat_loop())
    try:
        while True:
            chunk = await out.get()
            if chunk is _STREAM_END:
                break
            yield chunk
    finally:
        hb_task.cancel()
        if not agent_done:
            cancel_event.set()
        if not agent_task.done():
            agent_task.cancel()
        await asyncio.gather(agent_task, hb_task, return_exceptions=True)
