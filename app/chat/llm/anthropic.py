import json
from typing import Any, Dict, List, Optional

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

from app.chat.llm.adapter import ModelAdapter
from app.core.gateway_errors import GatewayChatError
from app.chat.llm.openai_compat import OpenAICompatAdapter
from app.chat.vision.refs import (
    EMPTY_VISION_CONTEXT,
    VisionBuildContext,
    iter_human_content_blocks,
    load_hydrated_image,
    should_hydrate_ref,
)
from app.chat.vision.types import IMAGE_REF_TYPE, TEXT_BLOCK_TYPE


def _human_to_anthropic_content(message: HumanMessage, ctx: VisionBuildContext) -> str | list[dict[str, Any]]:
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
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": effective_mime,
                    "data": b64,
                },
            }
        )
    if not out:
        return ""
    if len(out) == 1 and out[0].get("type") == "text":
        return str(out[0].get("text") or "")
    return out


class AnthropicAdapter(ModelAdapter):
    def build_params(
        self,
        messages: List[BaseMessage],
        gateway_model: str,
        tools: Optional[List[Dict[str, Any]]] = None,
        *,
        stream: bool = False,
        vision_ctx: VisionBuildContext | None = None,
    ) -> Dict[str, Any]:
        ctx = vision_ctx or EMPTY_VISION_CONTEXT
        system_parts: list[str] = []
        anthropic_messages: list[dict[str, Any]] = []

        for message in messages:
            if isinstance(message, SystemMessage):
                system_parts.append(str(message.content))
                continue
            if isinstance(message, HumanMessage):
                anthropic_messages.append(
                    {"role": "user", "content": _human_to_anthropic_content(message, ctx)}
                )
                continue
            if isinstance(message, AIMessage):
                blocks: list[dict[str, Any]] = []
                if message.content:
                    blocks.append({"type": "text", "text": str(message.content)})
                for call in message.tool_calls or []:
                    blocks.append(
                        {
                            "type": "tool_use",
                            "id": call["id"],
                            "name": call["name"],
                            "input": call.get("args") or {},
                        }
                    )
                anthropic_messages.append({"role": "assistant", "content": blocks or [{"type": "text", "text": ""}]})
                continue
            if isinstance(message, ToolMessage):
                anthropic_messages.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": message.tool_call_id,
                                "content": str(message.content),
                            }
                        ],
                    }
                )

        params: Dict[str, Any] = {
            "model": gateway_model,
            "max_tokens": 4096,
            "messages": anthropic_messages,
        }
        if system_parts:
            params["system"] = "\n\n".join(system_parts)
        if tools:
            params["tools"] = [
                {
                    "name": t["function"]["name"],
                    "description": t["function"].get("description", ""),
                    "input_schema": t["function"].get("parameters", {"type": "object", "properties": {}}),
                }
                for t in tools
                if t.get("type") == "function"
            ]
        if stream:
            params["stream"] = True
        return params

    def parse_stream_delta(self, delta, *, thinking, tag_state):
        return OpenAICompatAdapter().parse_stream_delta(delta, thinking=thinking, tag_state=tag_state)

    def parse_response(self, raw: Any) -> Dict[str, Any]:
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise GatewayChatError("gateway_protocol_error", "Anthropic response is not valid JSON", retryable=False) from exc

        if not isinstance(raw, dict):
            raise GatewayChatError("gateway_protocol_error", "Anthropic response is not an object", retryable=False)

        content_blocks = raw.get("content")
        if not isinstance(content_blocks, list):
            raise GatewayChatError("gateway_protocol_error", "Anthropic response is missing content", retryable=False)
        text_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        for block in content_blocks:
            if not isinstance(block, dict):
                raise GatewayChatError("gateway_protocol_error", "Anthropic content block is invalid", retryable=False)
            if block.get("type") == "text":
                text = block.get("text")
                if not isinstance(text, str):
                    raise GatewayChatError("gateway_protocol_error", "Anthropic text block is invalid", retryable=False)
                text_parts.append(text)
            elif block.get("type") == "tool_use":
                if not isinstance(block.get("id"), str) or not block["id"]:
                    raise GatewayChatError("gateway_protocol_error", "Anthropic tool call is missing id", retryable=False)
                if not isinstance(block.get("name"), str) or not block["name"]:
                    raise GatewayChatError("gateway_protocol_error", "Anthropic tool call is missing name", retryable=False)
                if not isinstance(block.get("input"), dict):
                    raise GatewayChatError("gateway_protocol_error", "Anthropic tool input is invalid", retryable=False)
                tool_calls.append(
                    {
                        "id": block["id"],
                        "name": block["name"],
                        "args": block["input"],
                    }
                )
            else:
                raise GatewayChatError("gateway_protocol_error", "Anthropic content block type is unknown", retryable=False)

        usage = raw.get("usage")
        if not isinstance(usage, dict):
            raise GatewayChatError("gateway_protocol_error", "Anthropic response is missing usage", retryable=False)
        input_tokens = usage.get("input_tokens")
        output_tokens = usage.get("output_tokens")
        if not isinstance(input_tokens, int) or not isinstance(output_tokens, int):
            raise GatewayChatError("gateway_protocol_error", "Anthropic usage is invalid", retryable=False)
        return {
            "content": "".join(text_parts),
            "tool_calls": tool_calls,
            "usage": {
                "prompt_tokens": input_tokens,
                "completion_tokens": output_tokens,
            },
        }
