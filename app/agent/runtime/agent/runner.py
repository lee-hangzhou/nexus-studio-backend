import asyncio
import json
from typing import Any, AsyncIterator, List, Union

from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.errors import GraphInterrupt
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command

from app.agent.runtime.agent.events import (
    AgentEvent,
    InvalidToolCall,
    ModelStepFinishedEvent,
    ModelTokenEvent,
    ToolFinishedEvent,
    ToolStartedEvent,
    TurnCompletedEvent,
    TurnFailedEvent,
)
from app.agent.runtime.agent.gateway_fail import stream_error_class_for_app_error
from app.agent.runtime.agent.tool_call_registry import ToolCallRegistry
from app.agent.runtime.llm.thinking import build_ai_message, reasoning_content_from_message
from app.agent.runtime.tools.result import INTERNAL, ToolResult
from app.agent.runtime.turn.tool_loop_guard import ONCE_PER_TURN_TOOL_NAMES
from app.agent.runtime.turn.trace import log_stage
from app.server.exceptions.base import AppError
from app.server.infra.gateway_errors import GatewayChatError
from app.server.infra.logger import log_exception


def _is_graph_interrupt(error: object) -> bool:
    if isinstance(error, GraphInterrupt):
        return True
    if isinstance(error, BaseExceptionGroup):
        return any(_is_graph_interrupt(exc) for exc in error.exceptions)
    cause = getattr(error, "__cause__", None)
    return _is_graph_interrupt(cause) if cause is not None else False


def _dedupe_tool_calls(message: AIMessage) -> AIMessage:
    calls = list(message.tool_calls or [])
    for call in calls:
        if (
            not isinstance(call, dict)
            or not isinstance(call.get("id"), str)
            or not call["id"]
            or not isinstance(call.get("name"), str)
            or not call["name"]
            or not isinstance(call.get("args"), dict)
        ):
            raise GatewayChatError(
                "gateway_protocol_error",
                "model tool call violates contract",
                retryable=False,
            )
    if len(calls) <= 1:
        return message
    seen_ids: set[str] = set()
    seen_sig: set[tuple] = set()
    seen_once_per_turn: set[str] = set()
    deduped = []
    for call in calls:
        cid = call["id"]
        tool_name = call["name"]
        sig = (
            tool_name,
            json.dumps(call["args"], sort_keys=True, ensure_ascii=False),
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
    raise GatewayChatError(
        "gateway_protocol_error",
        f"unsupported model message type: {type(message).__name__}",
        retryable=False,
    )


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
        call_id = item.get("call_id")
        name = item.get("name")
        raw_arguments = item.get("raw_arguments")
        parse_error = item.get("parse_error")
        if not all(isinstance(value, str) and value for value in (call_id, name, raw_arguments, parse_error)):
            raise GatewayChatError(
                "gateway_protocol_error",
                "invalid tool call metadata violates contract",
                retryable=False,
            )
        result.append(
            InvalidToolCall(
                call_id=call_id,
                name=name,
                raw_arguments=raw_arguments,
                parse_error=parse_error,
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
) -> AsyncIterator[AgentEvent]:
    step_index = 0
    stream_input = _normalize_agent_input(turn_input)
    answer_tokens_emitted = False
    registry = ToolCallRegistry()
    # Interrupt resume: first event is on_tool_start without a fresh model step in this stream.
    if isinstance(stream_input, Command):
        state = await agent.aget_state(config)
        registry.seed_from_messages(list(state.values.get("messages") or []))
    try:
        async for event in agent.astream_events(stream_input, config=config, version="v2"):
            kind = event.get("event")
            data = event.get("data") or {}
            metadata = event.get("metadata") or {}

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
                        yield ModelTokenEvent(
                            turn_id=turn_id,
                            step_index=step_index,
                            channel="answer",
                            text=assembled_content,
                        )
                    continue
                token_piece = generation_info.get("token_piece")
                if token_piece:
                    lane = token_piece.get("lane")
                    text = token_piece.get("text")
                    if lane not in {"answer", "think"} or not isinstance(text, str) or not text:
                        raise GatewayChatError(
                            "gateway_protocol_error",
                            "stream token metadata violates contract",
                            retryable=False,
                        )
                    if lane == "answer" and text:
                        answer_tokens_emitted = True
                    yield ModelTokenEvent(
                        turn_id=turn_id,
                        step_index=step_index,
                        channel=lane,
                        text=text,
                    )
                    continue
                message = getattr(chunk, "message", None)
                content = getattr(message, "content", None) if message is not None else None
                if isinstance(content, str) and content:
                    yield ModelTokenEvent(
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
                                    yield ModelTokenEvent(
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
                    yield ModelStepFinishedEvent(
                        turn_id=turn_id,
                        step_index=step_index,
                        ai_message=ai_message,
                        invalid_tool_calls=invalid_items,
                    )
                    if ai_message.tool_calls:
                        registry.register_from_model_step(
                            [dict(call) for call in ai_message.tool_calls]
                        )
                        step_index += 1

            elif kind == "on_tool_start":
                tool_input = data.get("input") or {}
                call_id = str(metadata.get("tool_call_id") or data.get("id") or "")
                tool_name = str(metadata.get("name") or event.get("name") or "")
                if not isinstance(tool_input, dict):
                    raise GatewayChatError(
                        "gateway_protocol_error",
                        "tool start event violates contract",
                        retryable=False,
                    )
                started = registry.on_start(
                    call_id=call_id,
                    tool_name=tool_name,
                    args=dict(tool_input),
                )
                log_stage(
                    "tool.start",
                    call_id=started.call_id,
                    tool_name=started.tool_name,
                )
                yield ToolStartedEvent(
                    turn_id=turn_id,
                    step_index=step_index,
                    call_id=started.call_id,
                    tool_name=started.tool_name,
                    tool_args=dict(started.args),
                )

            elif kind == "on_tool_end":
                output = data.get("output")
                content = _tool_output_text(output)
                call_id = ""
                if isinstance(output, ToolMessage):
                    call_id = output.tool_call_id or ""
                if not call_id:
                    call_id = str(metadata.get("tool_call_id") or "")
                tool_name = str(metadata.get("name") or event.get("name") or "")
                finished = registry.on_end(call_id=call_id, tool_name=tool_name)
                parsed = ToolResult.parse_tool_message(content)
                log_stage(
                    "tool.end",
                    call_id=finished.call_id,
                    tool_name=finished.tool_name,
                    error_type=parsed.error_type,
                    result_chars=len(content),
                )
                yield ToolFinishedEvent(
                    turn_id=turn_id,
                    step_index=step_index,
                    call_id=finished.call_id,
                    tool_name=finished.tool_name,
                    tool_args=dict(finished.args),
                    tool_result=content,
                    tool_error=not parsed.success,
                    error_class=parsed.error_type,
                )

            elif kind == "on_tool_error":
                error = data.get("error")
                # GraphInterrupt suspends the tool; leave registry open for Command(resume) seed.
                if _is_graph_interrupt(error):
                    continue
                call_id = str(metadata.get("tool_call_id") or "")
                tool_name = str(metadata.get("name") or event.get("name") or "")
                finished = registry.on_error(call_id=call_id, tool_name=tool_name)
                error_text = str(error) if error else "tool execution failed"
                envelope = ToolResult.fail(INTERNAL, detail=error_text).to_tool_message()
                log_stage(
                    "tool.end",
                    call_id=finished.call_id,
                    tool_name=finished.tool_name,
                    error_type="internal",
                    result_chars=len(envelope),
                )
                yield ToolFinishedEvent(
                    turn_id=turn_id,
                    step_index=step_index,
                    call_id=finished.call_id,
                    tool_name=finished.tool_name,
                    tool_result=envelope,
                    tool_args=dict(finished.args),
                    tool_error=True,
                    error_class="internal",
                )

        state = await agent.aget_state(config)
        final_messages = list(state.values.get("messages") or [])
        if not final_messages:
            raise RuntimeError("agent turn completed but checkpoint has no messages")
        yield TurnCompletedEvent(
            turn_id=turn_id,
            step_index=step_index,
            messages=final_messages,
        )
    except asyncio.CancelledError:
        raise
    except AppError as exc:
        error_class = stream_error_class_for_app_error(exc)
        log_exception(
            "agent.turn_failed",
            exc=exc,
            turn_id=turn_id,
            step_index=step_index,
            error_class=error_class,
        )
        yield TurnFailedEvent(
            turn_id=turn_id,
            step_index=step_index,
            error=exc.message,
            error_class=error_class,
        )
    except GatewayChatError as exc:
        log_exception(
            "agent.turn_failed",
            exc=exc,
            turn_id=turn_id,
            step_index=step_index,
            error_class=exc.error_type,
        )
        yield TurnFailedEvent(
            turn_id=turn_id,
            step_index=step_index,
            error=exc.detail,
            error_class=exc.error_type,
        )
    except Exception as exc:
        log_exception(
            "agent.turn_failed",
            exc=exc,
            turn_id=turn_id,
            step_index=step_index,
            error_class="internal",
        )
        yield TurnFailedEvent(
            turn_id=turn_id,
            step_index=step_index,
            error=str(exc),
            error_class="internal",
        )
