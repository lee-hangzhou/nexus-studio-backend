import asyncio
import json
from typing import Any, AsyncIterator, List, Union

from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.errors import GraphInterrupt
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command

from app.chat.agent.events import AgentEvent, AgentEventType, InvalidToolCall
from app.core.gateway_errors import GatewayChatError
from app.chat.agent.tool_recovery import (
    INTERNAL_TOOL_NAME,
    classify_tool_call,
    issue_to_internal_call,
)
from app.chat.llm.thinking import build_ai_message, reasoning_content_from_message
from app.chat.tools.result import ToolResult
from app.chat.turn.trace import log_stage
from app.core.config import settings
from app.core.logger import log_exception
from app.core.turn.tool_loop_guard import ONCE_PER_TURN_TOOL_NAMES


def _is_graph_interrupt(error: object) -> bool:
    if isinstance(error, GraphInterrupt):
        return True
    if isinstance(error, BaseExceptionGroup):
        return any(_is_graph_interrupt(exc) for exc in error.exceptions)
    cause = getattr(error, "__cause__", None)
    return _is_graph_interrupt(cause) if cause is not None else False


def _dedupe_tool_calls(message: AIMessage) -> AIMessage:
    calls = list(message.tool_calls or [])
    if len(calls) <= 1:
        return message
    seen_ids: set[str] = set()
    seen_sig: set[tuple] = set()
    seen_once_per_turn: set[str] = set()
    deduped = []
    for call in calls:
        cid = call.get("id") or ""
        tool_name = str(call.get("name") or "")
        sig = (
            tool_name,
            json.dumps(call.get("args") or {}, sort_keys=True, ensure_ascii=False),
        )
        if tool_name in ONCE_PER_TURN_TOOL_NAMES:
            if tool_name in seen_once_per_turn:
                continue
            seen_once_per_turn.add(tool_name)
        if (cid and cid in seen_ids) or sig in seen_sig:
            continue
        if cid:
            seen_ids.add(cid)
        seen_sig.add(sig)
        deduped.append(call)
    if len(deduped) == len(calls):
        return message
    return build_ai_message(
        content=str(message.content or ""),
        tool_calls=deduped,
        reasoning=reasoning_content_from_message(message),
    )


def _as_ai_message(message: AIMessage | AIMessageChunk | BaseMessage | None) -> AIMessage | None:
    if message is None:
        return None
    if isinstance(message, AIMessage):
        return message
    if isinstance(message, AIMessageChunk):
        return build_ai_message(
            content=str(message.content or ""),
            tool_calls=list(message.tool_calls or []),
        )
    return None


def _tool_output_text(output: object) -> str:
    if output is None:
        return ""
    if isinstance(output, ToolMessage):
        return str(output.content or "")
    content = getattr(output, "content", None)
    if isinstance(content, str):
        return content
    return str(output)


def _invalid_from_dict(items: list[dict[str, Any]]) -> list[InvalidToolCall]:
    result: list[InvalidToolCall] = []
    for item in items:
        result.append(
            InvalidToolCall(
                call_id=str(item.get("call_id", "")),
                name=str(item.get("name", "unknown")),
                raw_arguments=str(item.get("raw_arguments", "")),
                parse_error=str(item.get("parse_error", "")),
            )
        )
    return result


AgentTurnInput = Union[List[BaseMessage], Command]


def _normalize_agent_input(turn_input: AgentTurnInput) -> Command | dict[str, Any]:
    if isinstance(turn_input, Command):
        return turn_input
    if not turn_input:
        return {"messages": turn_input}
    has_command = any(isinstance(item, Command) for item in turn_input)
    if has_command:
        if len(turn_input) != 1 or not isinstance(turn_input[0], Command):
            raise ValueError(
                "agent turn input must not mix Command with other messages; "
                "pass Command alone for resume"
            )
        return turn_input[0]
    return {"messages": turn_input}


async def run_agent_turn_stream(
    agent: CompiledStateGraph,
    turn_input: AgentTurnInput,
    *,
    turn_id: str,
    config: RunnableConfig,
    tools_by_name: dict[str, Any] | None = None,
) -> AsyncIterator[AgentEvent]:
    step_index = 0
    stream_input = _normalize_agent_input(turn_input)
    if isinstance(stream_input, dict):
        latest_messages: list[BaseMessage] = list(stream_input.get("messages") or [])
    else:
        latest_messages = []
    answer_tokens_emitted = False
    pending_tool_calls: list[tuple[str, str]] = []
    synthetic_calls: dict[str, dict[str, Any]] = {}
    try:
        async for event in agent.astream_events(stream_input, config=config, version="v2"):
            kind = event.get("event")
            data = event.get("data") or {}
            metadata = event.get("metadata") or {}

            if kind == "on_chain_end":
                output = data.get("output")
                if isinstance(output, dict):
                    chain_messages = output.get("messages")
                    if isinstance(chain_messages, list) and chain_messages:
                        latest_messages = list(chain_messages)

            if kind == "on_chat_model_start":
                answer_tokens_emitted = False

            if kind == "on_chat_model_stream":
                chunk = data.get("chunk")
                if chunk is None:
                    continue
                generation_info = getattr(chunk, "generation_info", None) or {}
                if generation_info.get("assembled_step"):
                    assembled_content = generation_info.get("assembled_content")
                    if (
                        not answer_tokens_emitted
                        and isinstance(assembled_content, str)
                        and assembled_content
                    ):
                        yield AgentEvent(
                            type=AgentEventType.MODEL_TOKEN,
                            turn_id=turn_id,
                            step_index=step_index,
                            channel="answer",
                            text=assembled_content,
                        )
                    continue
                token_piece = generation_info.get("token_piece")
                if token_piece:
                    lane = token_piece.get("lane", "answer")
                    text = token_piece.get("text", "")
                    if lane == "answer" and text:
                        answer_tokens_emitted = True
                    yield AgentEvent(
                        type=AgentEventType.MODEL_TOKEN,
                        turn_id=turn_id,
                        step_index=step_index,
                        channel=lane,
                        text=text,
                    )
                    continue
                message = getattr(chunk, "message", None)
                content = getattr(message, "content", None) if message is not None else None
                if isinstance(content, str) and content:
                    yield AgentEvent(
                        type=AgentEventType.MODEL_TOKEN,
                        turn_id=turn_id,
                        step_index=step_index,
                        channel="answer",
                        text=content,
                    )

            elif kind in {"on_chat_model_end", "on_llm_end"}:
                output = data.get("output")
                ai_message: AIMessage | None = None
                invalid_items: list[InvalidToolCall] = []
                if output is not None:
                    generations = getattr(output, "generations", None)
                    if generations and generations[0]:
                        gen0 = generations[0]
                        if hasattr(gen0, "generation_info") and gen0.generation_info:
                            info = gen0.generation_info
                            if info.get("assembled_step"):
                                base = _as_ai_message(gen0.message if hasattr(gen0, "message") else None)
                                assembled_content = str(info.get("assembled_content") or "")
                                assembled_think = str(info.get("assembled_think_content") or "")
                                if base is not None:
                                    ai_message = build_ai_message(
                                        content=assembled_content,
                                        tool_calls=list(base.tool_calls or []),
                                        reasoning=assembled_think or None,
                                    )
                                if (
                                    not answer_tokens_emitted
                                    and assembled_content
                                    and not (ai_message and ai_message.tool_calls)
                                ):
                                    yield AgentEvent(
                                        type=AgentEventType.MODEL_TOKEN,
                                        turn_id=turn_id,
                                        step_index=step_index,
                                        channel="answer",
                                        text=assembled_content,
                                    )
                                    answer_tokens_emitted = True
                                invalid_items = _invalid_from_dict(info.get("invalid_tool_calls") or [])
                        if ai_message is None and hasattr(gen0, "message"):
                            ai_message = _as_ai_message(gen0.message)
                    if ai_message is None and isinstance(output, AIMessage):
                        ai_message = output

                if ai_message is not None:
                    ai_message = _dedupe_tool_calls(ai_message)
                    if settings.CHAT_TOOL_SELF_HEAL_ENABLED and tools_by_name:
                        rewritten_calls: list[dict[str, Any]] = []
                        recovery_attempt = sum(
                            1
                            for item in latest_messages
                            if isinstance(item, ToolMessage) and item.name == INTERNAL_TOOL_NAME
                        ) + 1
                        for call in ai_message.tool_calls or []:
                            issue = classify_tool_call(call, tools_by_name=tools_by_name)
                            rewritten_calls.append(
                                issue_to_internal_call(issue, recovery_attempt=recovery_attempt)
                                if issue is not None
                                else call
                            )
                        if rewritten_calls != list(ai_message.tool_calls or []):
                            ai_message = ai_message.model_copy(update={"tool_calls": rewritten_calls})
                    has_tools = bool(ai_message.tool_calls)
                    has_content = bool(str(ai_message.content or "").strip())
                    if not has_tools and not has_content and not invalid_items and step_index == 0:
                        continue
                    log_stage(
                        "model.step",
                        step_index=step_index,
                        tool_calls_count=len(ai_message.tool_calls or []),
                        content_len=len(str(ai_message.content or "")),
                        think_chars=len(reasoning_content_from_message(ai_message) or ""),
                    )
                    yield AgentEvent(
                        type=AgentEventType.MODEL_STEP_FINISHED,
                        turn_id=turn_id,
                        step_index=step_index,
                        ai_message=ai_message,
                        invalid_tool_calls=invalid_items,
                    )
                    if ai_message.tool_calls:
                        pending_tool_calls = [
                            (str(call.get("id") or ""), str(call.get("name") or ""))
                            for call in ai_message.tool_calls
                        ]
                        step_index += 1

            elif kind == "on_tool_start":
                tool_input = data.get("input") or {}
                call_id = str(metadata.get("tool_call_id") or data.get("id") or "")
                tool_name = str(metadata.get("name") or event.get("name") or "")
                if not call_id:
                    for index, (pending_id, pending_name) in enumerate(pending_tool_calls):
                        if pending_name == tool_name:
                            call_id = pending_id
                            pending_tool_calls.pop(index)
                            break
                synthetic = tool_name == INTERNAL_TOOL_NAME
                if synthetic and isinstance(tool_input, dict):
                    synthetic_calls[call_id] = dict(tool_input)
                    tool_name = str(tool_input.get("original_tool") or "unknown")
                log_stage("tool.start", call_id=call_id, tool_name=tool_name)
                yield AgentEvent(
                    type=AgentEventType.TOOL_STARTED,
                    turn_id=turn_id,
                    step_index=step_index,
                    call_id=call_id,
                    tool_name=tool_name,
                    tool_args={} if synthetic else (dict(tool_input) if isinstance(tool_input, dict) else {}),
                    synthetic=synthetic,
                    recovery_attempt=int(tool_input.get("recovery_attempt") or 1)
                    if synthetic and isinstance(tool_input, dict)
                    else None,
                    error_class=str(tool_input.get("error_code") or "invalid_arguments")
                    if synthetic and isinstance(tool_input, dict)
                    else None,
                )

            elif kind == "on_tool_end":
                output = data.get("output")
                content = _tool_output_text(output)
                call_id = ""
                if isinstance(output, ToolMessage):
                    call_id = output.tool_call_id or ""
                if not call_id:
                    call_id = str(metadata.get("tool_call_id") or "")
                parsed = ToolResult.parse_tool_message(content)
                tool_error = not parsed.success
                # Always forward error_type as error_class; some tools (e.g. signal_browser_blocked)
                # return success=True with a non-None error_type to signal the orchestrator.
                error_class = parsed.error_type
                synthetic_meta = synthetic_calls.pop(call_id, None)
                synthetic = synthetic_meta is not None
                tool_name = str(metadata.get("name") or event.get("name") or "")
                if synthetic_meta is not None:
                    tool_name = str(synthetic_meta.get("original_tool") or "unknown")
                    error_class = str(synthetic_meta.get("error_code") or "invalid_arguments")
                log_stage(
                    "tool.end",
                    call_id=call_id,
                    tool_name=tool_name,
                    error_type=parsed.error_type,
                    result_chars=len(content),
                )
                yield AgentEvent(
                    type=AgentEventType.TOOL_FINISHED,
                    turn_id=turn_id,
                    step_index=step_index,
                    call_id=call_id,
                    tool_name=tool_name,
                    tool_args={} if synthetic else None,
                    tool_result=parsed.display_text,
                    tool_error=tool_error,
                    synthetic=synthetic,
                    recovery_attempt=int(synthetic_meta.get("recovery_attempt") or 1)
                    if synthetic_meta is not None
                    else None,
                    error_class=error_class,
                )

            elif kind == "on_tool_error":
                error = data.get("error")
                if _is_graph_interrupt(error):
                    continue
                call_id = str(metadata.get("tool_call_id") or "")
                tool_name = str(metadata.get("name") or event.get("name") or "unknown")
                error = data.get("error")
                error_text = str(error) if error else "tool execution failed"
                log_stage(
                    "tool.end",
                    call_id=call_id,
                    tool_name=tool_name,
                    error_type="internal",
                    result_chars=len(error_text),
                )
                yield AgentEvent(
                    type=AgentEventType.TOOL_FINISHED,
                    turn_id=turn_id,
                    step_index=step_index,
                    call_id=call_id,
                    tool_name=tool_name,
                    tool_result=error_text,
                    tool_error=True,
                    error_class="internal",
                )

        state = await agent.aget_state(config)
        final_messages = list(state.values.get("messages") or [])
        if not final_messages:
            raise RuntimeError("agent turn completed but checkpoint has no messages")
        yield AgentEvent(
            type=AgentEventType.TURN_COMPLETED,
            turn_id=turn_id,
            step_index=step_index,
            messages=final_messages,
        )
    except asyncio.CancelledError:
        raise
    except GatewayChatError as exc:
        log_exception(
            "chat.agent.turn_failed",
            exc=exc,
            turn_id=turn_id,
            step_index=step_index,
            error_class=exc.error_type,
        )
        yield AgentEvent(
            type=AgentEventType.TURN_FAILED,
            turn_id=turn_id,
            step_index=step_index,
            error=exc.detail,
            error_class=exc.error_type,
        )
    except Exception as exc:
        log_exception(
            "chat.agent.turn_failed",
            exc=exc,
            turn_id=turn_id,
            step_index=step_index,
            error_class="user_cancelled" if "cancel" in str(exc).lower() else "internal",
        )
        yield AgentEvent(
            type=AgentEventType.TURN_FAILED,
            turn_id=turn_id,
            step_index=step_index,
            error=str(exc),
            error_class="user_cancelled" if "cancel" in str(exc).lower() else "internal",
        )
