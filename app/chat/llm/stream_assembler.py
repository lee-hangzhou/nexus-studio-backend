from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.outputs import ChatGenerationChunk

from app.chat.llm.tag_stream import push_tag_aware_text
from app.chat.llm.thinking import ThinkingConfig, ThinkingMode, ThinkTagStreamState, TokenPiece
from app.core.gateway_errors import GatewayChatError


@dataclass(frozen=True)
class InvalidToolCall:
    call_id: str
    name: str
    raw_arguments: str
    parse_error: str


@dataclass
class ToolSlot:
    index: int
    id: str | None = None
    name: str | None = None
    argument_fragments: list[str] = field(default_factory=list)


@dataclass
class AssembledStep:
    message: AIMessage
    invalid_tool_calls: list[InvalidToolCall]
    token_pieces: list[TokenPiece]
    finish_reason: str | None = None


class OpenAIStreamAssembler:
    def __init__(self, *, thinking: ThinkingConfig) -> None:
        self._thinking = thinking
        self._tag_state = ThinkTagStreamState()
        self._tool_slots: dict[int, ToolSlot] = {}
        self._finish_reason: str | None = None
        self._token_pieces: list[TokenPiece] = []

    def feed_sse_data(self, data: str) -> tuple[list[TokenPiece], list[ChatGenerationChunk]]:
        if data == "[DONE]":
            return [], []

        try:
            payload = json.loads(data)
        except json.JSONDecodeError as exc:
            raise GatewayChatError("gateway_protocol_error", "SSE data is not valid JSON", retryable=False) from exc

        if not isinstance(payload, dict):
            raise GatewayChatError("gateway_protocol_error", "SSE data is not an object", retryable=False)
        choices = payload.get("choices")
        if choices == []:
            # OpenAI-compatible streams may include a usage-only frame.
            if isinstance(payload.get("usage"), dict):
                return [], []
            raise GatewayChatError(
                "gateway_protocol_error",
                "SSE empty choices frame has no valid usage",
                retryable=False,
            )
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise GatewayChatError("gateway_protocol_error", "SSE data has no valid choice", retryable=False)
        choice = choices[0]
        delta = choice.get("delta")
        if not isinstance(delta, dict):
            raise GatewayChatError("gateway_protocol_error", "SSE choice is missing delta", retryable=False)
        finish_reason = choice.get("finish_reason")
        if finish_reason:
            self._finish_reason = finish_reason

        token_pieces = self._parse_delta_tokens(delta)
        self._token_pieces.extend(token_pieces)
        chunks: list[ChatGenerationChunk] = []

        raw_tool_calls = delta.get("tool_calls", [])
        if not isinstance(raw_tool_calls, list):
            raise GatewayChatError("gateway_protocol_error", "SSE tool_calls is not a list", retryable=False)
        for item in raw_tool_calls:
            if not isinstance(item, dict) or not isinstance(item.get("index"), int):
                raise GatewayChatError("gateway_protocol_error", "SSE tool call is missing index", retryable=False)
            index = item["index"]
            slot = self._tool_slots.setdefault(index, ToolSlot(index=index))
            if item.get("id"):
                slot.id = str(item["id"])
            function = item.get("function") or {}
            if function.get("name"):
                slot.name = str(function["name"])
            args_fragment = function.get("arguments")
            if isinstance(args_fragment, str) and args_fragment:
                slot.argument_fragments.append(args_fragment)
            chunks.append(
                ChatGenerationChunk(
                    message=AIMessageChunk(
                        content="",
                        tool_call_chunks=[
                            {
                                "index": index,
                                "id": slot.id,
                                "name": slot.name,
                                "args": args_fragment or "",
                                "type": "tool_call_chunk",
                            }
                        ],
                    )
                )
            )

        return token_pieces, chunks

    def _parse_delta_tokens(self, delta: dict[str, Any]) -> list[TokenPiece]:
        if self._thinking.mode == ThinkingMode.NONE:
            content = delta.get("content")
            return [TokenPiece(lane="answer", text=content)] if isinstance(content, str) and content else []

        if self._thinking.mode == ThinkingMode.REASONING_FIELD:
            pieces: list[TokenPiece] = []
            for field in self._thinking.reasoning_fields:
                value = delta.get(field)
                if isinstance(value, str) and value:
                    pieces.append(TokenPiece(lane="think", text=value))
            content = delta.get("content")
            if isinstance(content, str) and content:
                pieces.append(TokenPiece(lane="answer", text=content))
            if pieces:
                return pieces

        content = delta.get("content")
        if not isinstance(content, str) or not content:
            return []

        pieces: list[TokenPiece] = []
        for field in self._thinking.reasoning_fields:
            value = delta.get(field)
            if isinstance(value, str) and value:
                pieces.append(TokenPiece(lane="think", text=value))
        pieces.extend(push_tag_aware_text(content, self._tag_state))
        return pieces

    def finish(self) -> AssembledStep:
        if self._tag_state.pending:
            lane = "think" if self._tag_state.think_open else "answer"
            self._token_pieces.append(TokenPiece(lane=lane, text=self._tag_state.pending))
            self._tag_state.pending = ""

        valid_calls: list[dict[str, Any]] = []
        invalid_calls: list[InvalidToolCall] = []

        for slot in self._normalize_tool_slots():
            if not slot.id or not slot.name:
                raise GatewayChatError("gateway_protocol_error", "streamed tool call is missing id or name", retryable=False)
            call_id = slot.id
            name = slot.name
            raw_args = "".join(slot.argument_fragments)
            if not raw_args:
                raise GatewayChatError("gateway_protocol_error", "streamed tool call is missing arguments", retryable=False)
            try:
                args = json.loads(raw_args)
            except json.JSONDecodeError as exc:
                raise GatewayChatError(
                    "gateway_protocol_error",
                    "streamed tool arguments are not valid JSON",
                    retryable=False,
                ) from exc
            if not isinstance(args, dict):
                raise GatewayChatError(
                    "gateway_protocol_error",
                    "streamed tool arguments must be an object",
                    retryable=False,
                )
            valid_calls.append({"id": call_id, "name": name, "args": args})

        content = "".join(p.text for p in self._token_pieces if p.lane == "answer")
        message = AIMessage(content=content, tool_calls=valid_calls)
        return AssembledStep(
            message=message,
            invalid_tool_calls=invalid_calls,
            token_pieces=list(self._token_pieces),
            finish_reason=self._finish_reason,
        )

    def _normalize_tool_slots(self) -> list[ToolSlot]:
        """Merge gateway deltas where name/id and arguments arrive on separate indices."""
        ordered = sorted(self._tool_slots.values(), key=lambda item: item.index)
        if not ordered:
            return []

        merged: list[ToolSlot] = []

        def args_text(slot: ToolSlot) -> str:
            return "".join(slot.argument_fragments)

        def is_args_only(slot: ToolSlot) -> bool:
            return bool(args_text(slot).strip()) and not slot.id and not slot.name

        def is_header_only(slot: ToolSlot) -> bool:
            return bool(slot.name or slot.id) and not args_text(slot).strip()

        for slot in ordered:
            if merged and is_args_only(slot):
                prev = merged[-1]
                if is_header_only(prev) or (prev.name and not args_text(prev).strip()):
                    prev.argument_fragments.extend(slot.argument_fragments)
                    if slot.id and not prev.id:
                        prev.id = slot.id
                    continue

            merged.append(slot)

        result: list[ToolSlot] = []
        for slot in merged:
            if not slot.name and not slot.id and args_text(slot).strip():
                if result and result[-1].name and not args_text(result[-1]).strip():
                    result[-1].argument_fragments.extend(slot.argument_fragments)
                    continue
                raise GatewayChatError(
                    "gateway_protocol_error",
                    "streamed tool arguments have no tool id or name",
                    retryable=False,
                )
            if slot.name == "unknown" and not slot.id and args_text(slot).strip():
                if result and result[-1].name and result[-1].name != "unknown":
                    if not args_text(result[-1]).strip():
                        result[-1].argument_fragments.extend(slot.argument_fragments)
                        continue
                raise GatewayChatError(
                    "gateway_protocol_error",
                    "streamed tool arguments have an unknown tool name",
                    retryable=False,
                )
            result.append(slot)

        return result
