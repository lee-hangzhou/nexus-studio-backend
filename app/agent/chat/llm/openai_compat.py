import json
from typing import Any, Dict, List, Optional

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

from app.agent.chat.llm.adapter import ModelAdapter
from app.agent.chat.llm.tag_stream import push_tag_aware_text
from app.agent.chat.llm.thinking import (
    REASONING_CONTENT_KEY,
    ThinkingMode,
    TokenPiece,
    extract_reasoning_from_openai_message,
    reasoning_content_from_message,
)
from app.agent.chat.memory.message_validate import (
    has_unresolved_tool_calls,
    repair_unresolved_tool_calls,
    sanitize_tool_pairs,
    validate_langchain_tool_sequence,
)
from app.agent.chat.vision.refs import (
    EMPTY_VISION_CONTEXT,
    VisionBuildContext,
    count_hydratable_image_refs,
    iter_human_content_blocks,
    load_hydrated_image,
    should_hydrate_ref,
)
from app.server.chat.services.vision.types import IMAGE_REF_TYPE, TEXT_BLOCK_TYPE
from app.server.infra.config import settings
from app.server.infra.gateway_errors import GatewayChatError
from app.server.infra.logger import logger


def _human_to_openai_content(message: HumanMessage, ctx: VisionBuildContext) -> str | list[dict[str, Any]]:
    blocks = iter_human_content_blocks(message)
    out: list[dict[str, Any]] = []
    for block in blocks:
        block_type = block.get("type")
        if block_type == TEXT_BLOCK_TYPE:
            out.append({"type": "text", "text": str(block.get("text") or "")})
            continue
        if block_type != IMAGE_REF_TYPE:
            continue
        attachment_id = block.get("attachment_id")
        if not isinstance(attachment_id, int) or not should_hydrate_ref(attachment_id, ctx):
            continue
        mime_type = str(block.get("mime_type") or "image/jpeg")
        b64, effective_mime = load_hydrated_image(attachment_id, mime_type, ctx)
        out.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:{effective_mime};base64,{b64}"},
            }
        )
    if not out:
        return ""
    if len(out) == 1 and out[0].get("type") == "text":
        return str(out[0].get("text") or "")
    return out


def lc_to_openai_message(message: BaseMessage, *, vision_ctx: VisionBuildContext | None = None) -> Dict[str, Any]:
    ctx = vision_ctx or VisionBuildContext(frozenset(), {}, None)
    if isinstance(message, SystemMessage):
        return {"role": "system", "content": str(message.content)}
    if isinstance(message, HumanMessage):
        return {"role": "user", "content": _human_to_openai_content(message, ctx)}
    if isinstance(message, AIMessage):
        payload: Dict[str, Any] = {"role": "assistant", "content": message.content or ""}
        reasoning = reasoning_content_from_message(message)
        if reasoning:
            payload[REASONING_CONTENT_KEY] = reasoning
        if message.tool_calls:
            payload["tool_calls"] = [
                {
                    "id": call["id"],
                    "type": "function",
                    "function": {
                        "name": call["name"],
                        "arguments": json.dumps(call["args"], ensure_ascii=False)
                        if isinstance(call.get("args"), dict)
                        else str(call.get("args", "")),
                    },
                }
                for call in message.tool_calls
            ]
        return payload
    if isinstance(message, ToolMessage):
        return {
            "role": "tool",
            "tool_call_id": message.tool_call_id,
            "content": str(message.content),
        }
    raise TypeError(f"unsupported LangChain message type: {type(message).__name__}")


def validate_openai_messages(messages: List[Dict[str, Any]]) -> None:
    pending_ids: set[str] = set()
    for msg in messages:
        role = msg.get("role")
        if role == "assistant":
            pending_ids = {
                str(item.get("id") or "")
                for item in (msg.get("tool_calls") or [])
                if item.get("id")
            }
        elif role == "tool":
            call_id = str(msg.get("tool_call_id") or "")
            if not call_id or call_id not in pending_ids:
                logger.error(
                    "chat.openai.orphan_tool_payload",
                    tool_call_id=call_id,
                )
                raise ValueError(f"orphan OpenAI tool message: tool_call_id={call_id}")
        else:
            pending_ids = set()


class OpenAICompatAdapter(ModelAdapter):
    def build_params(
        self,
        messages: List[BaseMessage],
        gateway_model: str,
        tools: Optional[List[Dict[str, Any]]] = None,
        *,
        stream: bool = False,
        vision_ctx: VisionBuildContext | None = None,
    ) -> Dict[str, Any]:
        cleaned = list(messages)
        if has_unresolved_tool_calls(cleaned):
            cleaned = repair_unresolved_tool_calls(
                cleaned,
                source="outbound_last_resort",
            )
        cleaned = sanitize_tool_pairs(cleaned)
        validate_langchain_tool_sequence(cleaned)
        ctx = vision_ctx or EMPTY_VISION_CONTEXT
        hydratable_refs = count_hydratable_image_refs(cleaned, ctx)
        openai_messages = [lc_to_openai_message(m, vision_ctx=ctx) for m in cleaned]
        image_url_parts = sum(
            1
            for msg in openai_messages
            if msg.get("role") == "user"
            and isinstance(msg.get("content"), list)
            for part in msg["content"]
            if isinstance(part, dict) and part.get("type") == "image_url"
        )
        if hydratable_refs or ctx.hydrate_attachment_ids:
            logger.info(
                "chat.vision.build_params",
                hydrate_ids=len(ctx.hydrate_attachment_ids),
                path_count=len(ctx.attachment_paths),
                hydratable_refs=hydratable_refs,
                image_url_parts=image_url_parts,
            )
        if hydratable_refs and image_url_parts == 0:
            logger.warning(
                "chat.vision.hydrate_missed",
                hydratable_refs=hydratable_refs,
                hydrate_ids=len(ctx.hydrate_attachment_ids),
                path_count=len(ctx.attachment_paths),
            )
        validate_openai_messages(openai_messages)
        params: Dict[str, Any] = {
            "model": gateway_model,
            "messages": openai_messages,
            "max_tokens": settings.CHAT_COMPLETION_MAX_TOKENS,
        }
        if stream:
            params["stream"] = True
        if tools:
            params["tools"] = tools
            params["tool_choice"] = "auto"
        return params

    def parse_stream_delta(
        self,
        delta: Dict[str, Any],
        *,
        thinking,
        tag_state,
    ):
        if thinking.mode == ThinkingMode.NONE:
            content = delta.get("content")
            return [TokenPiece(lane="answer", text=content)] if isinstance(content, str) and content else []

        if thinking.mode == ThinkingMode.REASONING_FIELD:
            pieces = []
            for field in thinking.reasoning_fields:
                value = delta.get(field)
                if isinstance(value, str) and value:
                    pieces.append(TokenPiece(lane="think", text=value))
            content = delta.get("content")
            if isinstance(content, str) and content:
                pieces.append(TokenPiece(lane="answer", text=content))
            return pieces

        content = delta.get("content")
        if not isinstance(content, str) or not content:
            return []
        return push_tag_aware_text(content, tag_state)

    def parse_response(self, raw: Any) -> Dict[str, Any]:
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise GatewayChatError(
                    "gateway_protocol_error",
                    "chat completion response is not valid JSON",
                    retryable=False,
                ) from exc

        if not isinstance(raw, dict):
            raise GatewayChatError(
                "gateway_protocol_error",
                "chat completion response is not an object",
                retryable=False,
            )

        choices = raw.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise GatewayChatError(
                "gateway_protocol_error",
                "chat completion response has no valid choice",
                retryable=False,
            )

        message = choices[0].get("message")
        if not isinstance(message, dict):
            raise GatewayChatError(
                "gateway_protocol_error",
                "chat completion choice is missing message",
                retryable=False,
            )
        tool_calls: list[dict[str, Any]] = []
        raw_tool_calls = message.get("tool_calls", [])
        if not isinstance(raw_tool_calls, list):
            raise GatewayChatError("gateway_protocol_error", "message tool_calls is not a list", retryable=False)
        for item in raw_tool_calls:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"]:
                raise GatewayChatError("gateway_protocol_error", "tool call is missing id", retryable=False)
            fn = item.get("function")
            if not isinstance(fn, dict) or not isinstance(fn.get("name"), str) or not fn["name"]:
                raise GatewayChatError("gateway_protocol_error", "tool call is missing function name", retryable=False)
            args_raw = fn.get("arguments")
            if not isinstance(args_raw, str) or not args_raw:
                raise GatewayChatError("gateway_protocol_error", "tool call is missing arguments", retryable=False)
            try:
                args = json.loads(args_raw)
            except json.JSONDecodeError as exc:
                raise GatewayChatError("gateway_protocol_error", "tool call arguments are not valid JSON", retryable=False) from exc
            if not isinstance(args, dict):
                raise GatewayChatError("gateway_protocol_error", "tool call arguments must be an object", retryable=False)
            tool_calls.append(
                {
                    "id": item["id"],
                    "name": fn["name"],
                    "args": args,
                }
            )

        content = message.get("content")
        if content is None and tool_calls:
            content = ""
        elif not isinstance(content, str):
            raise GatewayChatError("gateway_protocol_error", "message content must be a string", retryable=False)

        reasoning = extract_reasoning_from_openai_message(message)
        usage = raw.get("usage")
        if usage is not None and not isinstance(usage, dict):
            raise GatewayChatError("gateway_protocol_error", "completion usage must be an object", retryable=False)

        return {
            "content": content,
            "tool_calls": tool_calls,
            "usage": usage or {},
            "reasoning_content": reasoning,
        }
