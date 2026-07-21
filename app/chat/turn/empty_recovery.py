"""Recover when the agent finishes with tool results but no assistant answer text."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from langchain_core.messages import AIMessage, BaseMessage, SystemMessage
from langchain_core.runnables import RunnableConfig

from app.chat.llm.gateway_chat_model import GatewayChatModel
from app.core.logger import log_exception, logger


@dataclass(frozen=True)
class EmptyRecoveryResult:
    text: str | None
    attempts: int
    failure_reason: str | None
    duration_ms: float


def _strip_trailing_empty_assistant(messages: list[BaseMessage]) -> list[BaseMessage]:
    trimmed = list(messages)
    while trimmed:
        last = trimmed[-1]
        if isinstance(last, AIMessage) and not last.tool_calls and not str(last.content or "").strip():
            trimmed.pop()
        else:
            break
    return trimmed


async def recover_empty_answer(
    llm: GatewayChatModel,
    messages: list[BaseMessage],
    *,
    config: RunnableConfig,
    system_prompt: str,
    max_attempts: int,
    timeout_sec: int,
    cancel_event: asyncio.Event | None = None,
) -> EmptyRecoveryResult:
    """Force a bounded, tool-free final answer from the existing turn context."""
    started = time.perf_counter()
    prepared = _strip_trailing_empty_assistant(messages)
    if not prepared or max_attempts <= 0:
        return EmptyRecoveryResult(
            text=None,
            attempts=0,
            failure_reason="no_context" if not prepared else "disabled",
            duration_ms=round((time.perf_counter() - started) * 1000, 2),
        )

    recovery_llm = llm.model_copy(update={"bound_tools": None})
    failure_reason = "empty"
    deadline = time.monotonic() + max(timeout_sec, 0)
    attempts_started = 0
    for attempt in range(1, max_attempts + 1):
        if cancel_event is not None and cancel_event.is_set():
            failure_reason = "cancelled"
            break
        remaining_timeout = deadline - time.monotonic()
        if remaining_timeout <= 0:
            failure_reason = "timeout"
            break
        strict = attempt > 1
        recovery_instruction = (
            "你正在恢复一个没有最终回答的 Agent 回合。禁止调用任何工具。"
            "请仅根据已有用户消息和工具结果，直接输出面向用户的最终回答。"
            "证据不足时明确说明限制，禁止编造工具结果。"
        )
        if strict:
            recovery_instruction += (
                " 必须输出非空纯文本，不要解释恢复过程。"
                "禁止输出任何工具调用、XML、DSML 或 invoke 标记。"
            )
        recovery_messages = [
            SystemMessage(content=f"{system_prompt}\n\n{recovery_instruction}"),
            *prepared,
        ]
        logger.info(
            "chat.empty_recovery.attempt",
            attempt=attempt,
            max_attempts=max_attempts,
        )
        model_task = asyncio.create_task(
            recovery_llm._agenerate(
                recovery_messages,
                config=config,
                tools=[],
            )
        )
        attempts_started += 1
        cancel_task = (
            asyncio.create_task(cancel_event.wait())
            if cancel_event is not None
            else None
        )
        try:
            wait_for = [model_task]
            if cancel_task is not None:
                wait_for.append(cancel_task)
            done, _ = await asyncio.wait(
                wait_for,
                timeout=remaining_timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if cancel_task is not None and cancel_task in done:
                failure_reason = "cancelled"
                model_task.cancel()
                await asyncio.gather(model_task, return_exceptions=True)
                break
            if model_task not in done:
                failure_reason = "timeout"
                model_task.cancel()
                await asyncio.gather(model_task, return_exceptions=True)
                logger.warning("chat.empty_recovery.timeout", attempt=attempt)
                break
            result = model_task.result()
        except asyncio.TimeoutError:
            failure_reason = "timeout"
            logger.warning("chat.empty_recovery.timeout", attempt=attempt)
            break
        except asyncio.CancelledError:
            model_task.cancel()
            await asyncio.gather(model_task, return_exceptions=True)
            raise
        except Exception as exc:
            failure_reason = "gateway_error"
            log_exception("chat.empty_recovery.failed", exc=exc, attempt=attempt)
            continue
        finally:
            if cancel_task is not None:
                cancel_task.cancel()
                await asyncio.gather(cancel_task, return_exceptions=True)
        if not result.generations:
            failure_reason = "no_generation"
            continue
        message = result.generations[0].message
        if not isinstance(message, AIMessage):
            failure_reason = "invalid_message"
            continue
        if message.tool_calls:
            failure_reason = "unexpected_tool_calls"
            logger.warning(
                "chat.empty_recovery.unexpected_tool_calls",
                attempt=attempt,
                tool_calls_count=len(message.tool_calls),
            )
            continue
        raw_text = str(message.content or "").strip()
        if not raw_text:
            failure_reason = "empty"
            logger.warning("chat.empty_recovery.still_empty", attempt=attempt)
            continue
        return EmptyRecoveryResult(
            text=raw_text,
            attempts=attempts_started,
            failure_reason=None,
            duration_ms=round((time.perf_counter() - started) * 1000, 2),
        )

    return EmptyRecoveryResult(
        text=None,
        attempts=attempts_started,
        failure_reason=failure_reason,
        duration_ms=round((time.perf_counter() - started) * 1000, 2),
    )
