from __future__ import annotations

import asyncio
import contextlib
import json
import time
from datetime import datetime, timezone
from typing import Any, AsyncIterator
from uuid import uuid4

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.types import Command

from app.agent.canvas.agent.factory import build_canvas_agent
from app.agent.canvas.memory.store import canvas_runnable_config
from app.agent.canvas.turn.checkpoint import repair_canvas_checkpoint_if_needed
from app.agent.canvas.turn.facts import CanvasTurnFacts
from app.agent.canvas.turn.generation_hub import canvas_generation_hub
from app.agent.canvas.turn.guards import CanvasTurnGuards
from app.agent.canvas.turn.lock import project_turn_lock
from app.agent.canvas.turn.memory_background import schedule_canvas_memory_extract
from app.agent.canvas.turn.persistence import (
    persist_canvas_assistant_message,
    persist_canvas_tool_step,
    persist_canvas_user_message,
)
from app.agent.chat.agent.events import AgentEventType
from app.agent.chat.agent.runner import run_agent_turn_stream
from app.agent.chat.llm.gateway_chat_model import GatewayChatModel
from app.agent.chat.llm.registry import get_model_spec
from app.agent.chat.stream.agent_frames import chunk_text, frames_from_agent_event
from app.agent.chat.stream.encoder import encode_sse_frame
from app.agent.chat.stream.frames import StreamFrame, StreamFrameType, create_stream_frame
from app.agent.chat.tools.result import ToolResult
from app.contracts.metadata import CanvasToolStepMetadata
from app.agent.runtime.checkpointer import get_chat_checkpointer
from app.agent.runtime.turn.tool_loop_guard import TurnToolLoopGuard
from app.server.infra.config import settings
from app.server.infra.logger import bind_context, log_exception, logger
from app.server.exceptions.base import AppError
from app.server.exceptions.codes import ErrorCode
from app.server.chat.domain.stream_enums import StreamErrorCode
from app.server.canvas.persistence.nodes import CanvasNodes
from app.server.canvas.persistence.project_meta import CanvasProjectMeta
from app.server.projects.persistence.projects import Projects

CANVAS_AGENT_MODEL_KEY = "gpt-5.5"

_STREAM_END: None = None


def _canvas_thread_id(project_id: int) -> str:
    """固定 thread_id, 多轮共用 checkpointer 上下文"""
    return f"{settings.CANVAS_CHECKPOINT_THREAD_PREFIX}-{project_id}"


async def _load_turn_facts(project_id: int, *, mode: str, enable_tools: bool) -> CanvasTurnFacts:
    """加载 turn 开始前 revision, 节点数, 边数等轻量事实"""
    meta = await CanvasProjectMeta.filter(project_id=project_id).first()
    revision = int(meta.revision) if meta else 0
    node_count = int(meta.node_count) if meta else 0
    edge_count = int(meta.edge_count) if meta else 0
    pending_generation_count = await CanvasNodes.filter(
        project_id=project_id,
        deleted_at__isnull=True,
        status="running",
    ).count()
    return CanvasTurnFacts(
        revision=revision,
        node_count=node_count,
        edge_count=edge_count,
        mode=mode,  # type: ignore[arg-type]
        enable_tools=enable_tools,
        attachments_not_ready=False,
        pending_generation_count=pending_generation_count,
    )


async def _touch_project(project_id: int) -> None:
    """刷新项目 updated_at, 供列表按最近活动排序"""
    await Projects.filter(id=project_id).update(updated_at=datetime.now(timezone.utc))


def _parse_tool_payload(raw: str) -> dict[str, Any] | None:
    """解析 ToolResult JSON payload, 失败返回 None"""
    parsed = ToolResult.parse_tool_message(raw)
    if not parsed.success:
        return None
    try:
        return json.loads(parsed.output or "{}")
    except json.JSONDecodeError:
        return None


def _current_turn_message_slice(messages: list) -> list:
    """取最后一个 HumanMessage 之后的消息, 避免回填历史轮次正文"""
    last_human = -1
    for index, message in enumerate(messages or []):
        if isinstance(message, HumanMessage):
            last_human = index
    if last_human < 0:
        return list(messages or [])
    return list(messages)[last_human + 1 :]


async def _emit_assistant_text_backfill(
    messages: list,
    *,
    answer_parts: list[str],
    emit,
) -> None:
    """事件流无 token 时从本轮 AIMessage 回填正文给前端"""
    if answer_parts:
        return
    for message in reversed(_current_turn_message_slice(messages)):
        if not isinstance(message, AIMessage):
            continue
        if message.tool_calls:
            continue
        text = str(message.content or "").strip()
        if not text:
            continue
        for piece in chunk_text(text):
            answer_parts.append(piece)
            await emit(
                create_stream_frame(
                    type=StreamFrameType.TOKEN,
                    protocol_version=settings.CHAT_SSE_PROTOCOL_VERSION,
                    channel="answer",
                    text=piece,
                )
            )
        return


async def _emit_canvas_patch_from_tool(event: Any, emit) -> None:
    """把工具结果中的画布变更转成 SSE frame"""
    name = getattr(event, "tool_name", None) or ""
    if name not in ("apply_canvas_patch", "submit_node_generation"):
        return
    raw = getattr(event, "tool_result", "") or ""
    parsed = ToolResult.parse_tool_message(raw)
    if not parsed.success:
        return
    data = _parse_tool_payload(raw)
    if not data:
        return
    if name == "submit_node_generation":
        # submit_node_generation 只通知前端该节点进入 running
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


async def _emit_interrupts(agent, config, emit, *, turn_id: str) -> bool:
    """把 LangGraph 人工确认中断映射为前端待确认工具调用"""
    snap = await agent.aget_state(config)
    if not snap.interrupts:
        return False
    for intr in snap.interrupts:
        value = intr.value if hasattr(intr, "value") else intr
        if not isinstance(value, dict):
            continue
        action_requests = value.get("action_requests")
        if isinstance(action_requests, list):
            for index, item in enumerate(action_requests):
                if not isinstance(item, dict):
                    continue
                await emit(
                    create_stream_frame(
                        type=StreamFrameType.TOOL_PENDING,
                        turn_id=turn_id,
                        call_id=str(item.get("id") or item.get("call_id") or index),
                        name=str(item.get("name") or ""),
                        summary=str(item.get("description") or item.get("args") or ""),
                    )
                )
            continue
        await emit(
            create_stream_frame(
                type=StreamFrameType.TOOL_PENDING,
                turn_id=turn_id,
                call_id=str(value.get("call_id") or ""),
                name=str(value.get("name") or ""),
                summary=str(value.get("summary") or ""),
            )
        )
    return True


async def stream_canvas_turn(
    *,
    project_id: int,
    user_id: int,
    content: str,
    model_key: str,
    client_turn_id: str | None,
    mode: str = "auto",
    enable_tools: bool,
    cancel_event: asyncio.Event,
    turn_id: str | None = None,
    lock_held: bool = False,
) -> AsyncIterator[str]:
    """执行一轮 Canvas Agent, SSE 输出 token, 工具, 画布, 生成事件"""
    turn_id = turn_id or uuid4().hex
    bind_context(user_id=user_id, project_id=project_id, turn_id=turn_id)
    requested_model_key = model_key
    # 画布 Agent 固定生产模型, 忽略前端传入的 model_key
    model_key = CANVAS_AGENT_MODEL_KEY
    out: asyncio.Queue[str | None] = asyncio.Queue()
    # 订阅 generation hub, callback 异步把节点状态推入同一 SSE
    gen_queue = canvas_generation_hub.subscribe(project_id)

    async def emit(frame: StreamFrame) -> None:
        """把 StreamFrame 编码后写入内部队列"""
        await out.put(encode_sse_frame(frame))

    async def agent_loop() -> None:
        """后台运行 Agent, 把 LangGraph 事件翻译成 SSE frame"""
        answer_parts: list[str] = []
        tool_calls_count = 0
        terminated_by = "error"
        turn_failed = False
        turn_failed_error = ""
        turn_id_holder = {"turn_id": turn_id}
        agent = None
        config = None
        try:
            if not lock_held:
                # 未持锁时先 acquire, 降低 revision 冲突
                await project_turn_lock.acquire(project_id, turn_id)
            facts = await _load_turn_facts(project_id, mode=mode, enable_tools=enable_tools)
            logger.info(
                "canvas.turn.start",
                project_id=project_id,
                turn_id=turn_id,
                mode=mode,
                model_key=model_key,
                requested_model_key=requested_model_key,
                revision=facts.revision,
                enable_tools=enable_tools,
            )
            await persist_canvas_user_message(
                project_id=project_id,
                user_id=user_id,
                content=content,
                client_turn_id=client_turn_id,
                turn_id=turn_id,
            )
            spec = get_model_spec(model_key)
            llm = GatewayChatModel(
                model_key=model_key,
                spec=spec,
                cancel_event=cancel_event,
            )
            loop_guard = TurnToolLoopGuard(surface="canvas")
            agent, _ = await build_canvas_agent(
                llm,
                project_id=project_id,
                user_id=user_id,
                checkpointer=get_chat_checkpointer(),
                enable_tools=enable_tools,
                mode=mode,
                turn_id_holder=turn_id_holder,
                loop_guard=loop_guard,
            )
            config = canvas_runnable_config(
                thread_id=_canvas_thread_id(project_id),
                user_id=user_id,
                project_id=project_id,
                mode=mode,
            )
            await repair_canvas_checkpoint_if_needed(
                agent,
                config,
                project_id=project_id,
                turn_id=turn_id,
            )
            guards = CanvasTurnGuards(
                max_model_steps=settings.CANVAS_MAX_ITERATIONS,
                max_tool_calls=settings.CANVAS_MAX_TOOL_CALLS,
                wall_clock_sec=settings.CANVAS_TURN_WALL_CLOCK_SEC,
                tool_repeat_guard=settings.CANVAS_TOOL_REPEAT_GUARD,
            )

            async for event in run_agent_turn_stream(
                agent,
                [HumanMessage(content=content)],
                turn_id=turn_id,
                config=config,
            ):
                if cancel_event.is_set():
                    break
                if not guards.check_wall_clock():
                    break
                for frame in frames_from_agent_event(event, turn_id=turn_id):
                    if frame.type == StreamFrameType.TOKEN and frame.text:
                        # 剥离伪工具调用文本, 避免前端误以为已执行工具
                        cleaned = frame.text
                        if cleaned:
                            answer_parts.append(cleaned)
                            if cleaned != frame.text:
                                frame = frame.model_copy(update={"text": cleaned})
                    await emit(frame)
                if event.type == AgentEventType.MODEL_STEP_FINISHED:
                    # 每步检查迭代上限, 参数解析失败以工具步骤展示
                    guards.on_model_step_finished()
                    if guards.exceeded_model_steps():
                        break
                    if event.invalid_tool_calls:
                        valid_calls = (
                            list(event.ai_message.tool_calls or [])
                            if event.ai_message is not None
                            else []
                        )
                        for inv in event.invalid_tool_calls:
                            preview = (
                                f"参数解析失败: {inv.parse_error}\n"
                                f"raw: {inv.raw_arguments[:300]}"
                            )
                            await emit(
                                create_stream_frame(
                                    type=StreamFrameType.TOOL_START,
                                    protocol_version=settings.CHAT_SSE_PROTOCOL_VERSION,
                                    call_id=inv.call_id,
                                    name=inv.name,
                                    args={"parse_error": inv.parse_error},
                                )
                            )
                            await emit(
                                create_stream_frame(
                                    type=StreamFrameType.TOOL_END,
                                    protocol_version=settings.CHAT_SSE_PROTOCOL_VERSION,
                                    call_id=inv.call_id,
                                    name=inv.name,
                                    ok=False,
                                    preview=preview,
                                )
                            )
                        if not valid_calls:
                            first = event.invalid_tool_calls[0]
                            logger.error(
                                "canvas.turn.tool_parse_fatal",
                                project_id=project_id,
                                turn_id=turn_id,
                                tool_name=first.name,
                                parse_error=first.parse_error,
                                invalid_tool_calls=len(event.invalid_tool_calls),
                            )
                            await emit(
                                create_stream_frame(
                                    type=StreamFrameType.ERROR,
                                    protocol_version=settings.CHAT_SSE_PROTOCOL_VERSION,
                                    code="tool_parse_fatal",
                                    message=f"工具参数解析失败（{first.name}）",
                                )
                            )
                            terminated_by = "error"
                            break
                        logger.warning(
                            "canvas.turn.tool_args_partial_invalid",
                            project_id=project_id,
                            turn_id=turn_id,
                            invalid_tool_calls=len(event.invalid_tool_calls),
                            valid_tool_calls=len(valid_calls),
                        )
                    if event.ai_message is not None:
                        # 无流式 token 时从无工具调用的最终 AIMessage 补正文
                        step_answer = str(event.ai_message.content or "").strip()
                        if step_answer and not event.ai_message.tool_calls and not answer_parts:
                            for piece in chunk_text(step_answer):
                                answer_parts.append(piece)
                                await emit(
                                    create_stream_frame(
                                        type=StreamFrameType.TOKEN,
                                        protocol_version=settings.CHAT_SSE_PROTOCOL_VERSION,
                                        channel="answer",
                                        text=piece,
                                    )
                                )
                if event.type == AgentEventType.TOOL_FINISHED and enable_tools:
                    # 工具结束后持久化步骤, 同步 patch 或生成结果给前端
                    tool_calls_count += 1
                    action = guards.on_tool_finished(
                        event.tool_name,
                        event.error_class,
                    )
                    await persist_canvas_tool_step(
                        project_id=project_id,
                        user_id=user_id,
                        turn_id=turn_id,
                        step=CanvasToolStepMetadata(
                            call_id=event.call_id,
                            name=event.tool_name,
                            ok=not event.tool_error,
                            preview=event.tool_result[:500],
                            error_type=event.error_class,
                        ),
                    )
                    await _emit_canvas_patch_from_tool(event, emit)
                    if action == "stop_turn":
                        break
                if event.type == AgentEventType.TURN_FAILED:
                    turn_failed = True
                    terminated_by = "turn_failed"
                    turn_failed_error = event.error
                    logger.error(
                        "canvas.turn.agent_failed",
                        project_id=project_id,
                        turn_id=turn_id,
                        step_index=event.step_index,
                        error=turn_failed_error,
                        error_class=event.error_class,
                    )
                    await repair_canvas_checkpoint_if_needed(
                        agent,
                        config,
                        project_id=project_id,
                        turn_id=turn_id,
                        reason=event.error_class,
                    )
                    break
                if event.type == AgentEventType.TURN_COMPLETED:
                    terminated_by = "completed"
                    await _emit_assistant_text_backfill(
                        list(event.messages),
                        answer_parts=answer_parts,
                        emit=emit,
                    )
                    schedule_canvas_memory_extract(
                        messages=list(event.messages),
                        user_id=user_id,
                        project_id=project_id,
                    )

            interrupted = await _emit_interrupts(agent, config, emit, turn_id=turn_id)
            if interrupted:
                terminated_by = "interrupted"
            elif not cancel_event.is_set():
                if turn_failed:
                    await _touch_project(project_id)
                else:
                    answer_text = "".join(answer_parts).strip()
                    if not answer_text:
                        logger.warning(
                            "canvas.turn.empty",
                            answer_chars=0,
                            think_chars=0,
                            project_id=project_id,
                            turn_id=turn_id,
                        )
                        await emit(
                            create_stream_frame(
                                type=StreamFrameType.ERROR,
                                code="empty_response",
                                message="模型未返回有效回答",
                                turn_id=turn_id,
                            )
                        )
                        terminated_by = "error"
                        return
                    await persist_canvas_assistant_message(
                        project_id=project_id,
                        user_id=user_id,
                        content=answer_text,
                        client_turn_id=client_turn_id,
                        turn_id=turn_id,
                        tool_calls_count=tool_calls_count,
                    )
                    await _touch_project(project_id)
                    await emit(create_stream_frame(type=StreamFrameType.DONE, turn_id=turn_id))
            logger.info(
                "canvas.turn.done",
                project_id=project_id,
                turn_id=turn_id,
                terminated_by=terminated_by,
                tool_calls_count=tool_calls_count,
            )
        except AppError as exc:
            code = {
                int(ErrorCode.CANVAS_PROJECT_BUSY): "canvas_project_busy",
                int(ErrorCode.CANVAS_DUPLICATE_TURN): "canvas_duplicate_turn",
            }.get(exc.code, "internal")
            await emit(
                create_stream_frame(
                    type=StreamFrameType.ERROR,
                    code=code,
                    message=exc.message,
                    turn_id=turn_id,
                    data=exc.details or {},
                )
            )
        except Exception as exc:
            log_exception(
                "canvas.turn.error",
                exc=exc,
                project_id=project_id,
                turn_id=turn_id,
            )
            await emit(
                create_stream_frame(
                    type=StreamFrameType.ERROR,
                    code=StreamErrorCode.INTERNAL.value,
                    message="turn failed",
                    turn_id=turn_id,
                )
            )
        finally:
            if agent is not None and config is not None and (
                cancel_event.is_set() or terminated_by != "completed"
            ):
                with contextlib.suppress(Exception):
                    await repair_canvas_checkpoint_if_needed(
                        agent,
                        config,
                        project_id=project_id,
                        turn_id=turn_id,
                    )
            canvas_generation_hub.unsubscribe(project_id, gen_queue)
            if not lock_held:
                await project_turn_lock.release(project_id, turn_id)
            await out.put(_STREAM_END)

    async def fanout_generation() -> None:
        """把 callback 或 runner 发布的生成进度转发到本轮 SSE"""
        try:
            while True:
                item = await gen_queue.get()
                if item is None:
                    break
                patch = item.get("canvas_patch")
                if patch:
                    await emit(create_stream_frame(type=StreamFrameType.CANVAS_PATCH, data=patch, turn_id=turn_id))
                progress = item.get("progress")
                if progress:
                    await emit(
                        create_stream_frame(
                            type=StreamFrameType.GENERATION_PROGRESS,
                            turn_id=turn_id,
                            data=progress,
                        )
                    )
        except asyncio.CancelledError:
            pass

    fanout_task = asyncio.create_task(fanout_generation())
    task = asyncio.create_task(agent_loop())
    try:
        while True:
            if cancel_event.is_set():
                await emit(create_stream_frame(type=StreamFrameType.CANCELLED, turn_id=turn_id, reason="user_cancel"))
                break
            try:
                item = await asyncio.wait_for(out.get(), timeout=float(settings.CANVAS_HEARTBEAT_INTERVAL_SEC))
            except asyncio.TimeoutError:
                # 心跳保活, 避免长生成期间连接被误判断开
                await emit(
                    create_stream_frame(
                        type=StreamFrameType.HEARTBEAT,
                        turn_id=turn_id,
                        ts=int(time.time()),
                    )
                )
                continue
            if item is _STREAM_END:
                break
            yield item
    finally:
        fanout_task.cancel()
        await gen_queue.put(None)
        if not task.done():
            task.cancel()


async def stream_canvas_resume(
    *,
    project_id: int,
    user_id: int,
    turn_id: str,
    tool_call_id: str,
    action: str,
    cancel_event: asyncio.Event,
    lock_held: bool = False,
) -> AsyncIterator[str]:
    """恢复手动模式下被中断的工具调用"""
    out: asyncio.Queue[str | None] = asyncio.Queue()

    async def emit(frame: StreamFrame) -> None:
        """把 StreamFrame 编码后写入内部队列"""
        await out.put(encode_sse_frame(frame))

    async def loop() -> None:
        """把 confirm 或 reject 喂回 LangGraph, 继续输出工具与画布事件"""
        try:
            if not lock_held:
                await project_turn_lock.acquire(project_id, turn_id)
            spec = get_model_spec(CANVAS_AGENT_MODEL_KEY)
            llm = GatewayChatModel(
                model_key=CANVAS_AGENT_MODEL_KEY,
                spec=spec,
                cancel_event=cancel_event,
            )
            turn_id_holder = {"turn_id": turn_id}
            loop_guard = TurnToolLoopGuard(surface="canvas")
            agent, _ = await build_canvas_agent(
                llm,
                project_id=project_id,
                user_id=user_id,
                checkpointer=get_chat_checkpointer(),
                enable_tools=True,
                mode="manual",
                turn_id_holder=turn_id_holder,
                loop_guard=loop_guard,
            )
            config = canvas_runnable_config(
                thread_id=_canvas_thread_id(project_id),
                user_id=user_id,
                project_id=project_id,
                mode="manual",
            )
            decision = (
                {"type": "approve"}
                if action == "confirm"
                else {"type": "reject", "message": "user rejected tool execution"}
            )
            # LangGraph 中断恢复用 Command resume, 沿用同一 checkpoint thread
            resume_payload = {"decisions": [decision]}
            async for event in run_agent_turn_stream(
                agent,
                [Command(resume=resume_payload)],
                turn_id=turn_id,
                config=config,
            ):
                for frame in frames_from_agent_event(event, turn_id=turn_id):
                    await emit(frame)
                if event.type == AgentEventType.TOOL_FINISHED:
                    await _emit_canvas_patch_from_tool(event, emit)
            interrupted = await _emit_interrupts(agent, config, emit, turn_id=turn_id)
            if not interrupted:
                await emit(create_stream_frame(type=StreamFrameType.DONE, turn_id=turn_id))
        finally:
            if not lock_held:
                await project_turn_lock.release(project_id, turn_id)
            await out.put(_STREAM_END)

    task = asyncio.create_task(loop())
    try:
        while True:
            if cancel_event.is_set():
                yield encode_sse_frame(
                    create_stream_frame(
                        type=StreamFrameType.CANCELLED,
                        turn_id=turn_id,
                        reason="user_cancel",
                    )
                )
                break
            try:
                item = await asyncio.wait_for(out.get(), timeout=float(settings.CANVAS_HEARTBEAT_INTERVAL_SEC))
            except asyncio.TimeoutError:
                yield encode_sse_frame(
                    create_stream_frame(type=StreamFrameType.HEARTBEAT, turn_id=turn_id, ts=int(time.time()))
                )
                continue
            if item is _STREAM_END:
                break
            yield item
    finally:
        if not task.done():
            task.cancel()
