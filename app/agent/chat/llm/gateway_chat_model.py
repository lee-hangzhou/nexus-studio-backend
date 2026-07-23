import asyncio
from typing import Any, AsyncIterator, Dict, List, Optional, Sequence
from langchain_core.callbacks import AsyncCallbackManagerForLLMRun, CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import Field

from app.agent.chat.llm import get_adapter
from app.server.exceptions.base import AppError
from app.server.exceptions.codes import ErrorCode
from app.server.infra.gateway_errors import GatewayChatError
from app.agent.runtime.agent.gateway_fail import stream_error_class_for_app_error
from app.agent.chat.llm.registry import ModelSpec
from app.agent.chat.llm.stream_assembler import OpenAIStreamAssembler
from app.agent.chat.llm.thinking import build_ai_message
from app.agent.chat.memory.message_limits import truncate_oversized_tool_messages
from app.agent.chat.tools.schema_sanitize import sanitize_tool_schema
from app.agent.chat.vision.refs import VisionBuildContext, build_vision_context
from app.server.infra.config import settings
from app.server.infra.gateway import gateway_client
from app.server.infra.logger import logger

_RETRYABLE_GATEWAY_APP_CODES = {
    int(ErrorCode.SERVICE_UNAVAILABLE),
    int(ErrorCode.GATEWAY_QUOTA_OR_RATE_LIMITED),
    int(ErrorCode.GATEWAY_SUBMIT_ERROR),
}


class GatewayChatModel(BaseChatModel):
    """项目模型网关和 LangChain/LangGraph Agent 之间的适配器。

    LangGraph 只认识 LangChain 的 ``BaseChatModel``。这个类负责把
    LangChain 的消息和工具转换成项目网关的 chat-completions 请求，
    再把网关响应转换回带文本和工具调用的 ``AIMessage``。
    """

    # 用户/API 选择的模型 key，用于模型注册表查询和日志记录。
    model_key: str
    # 模型注册表元信息：网关模型名、模型家族、上下文预算、思考配置、视觉能力等。
    spec: ModelSpec
    # LangChain 绑定到当前模型实例上的 OpenAI 风格工具 schema。
    # 注意区分 ``None`` 和 ``[]``：``[]`` 表示显式禁用工具。
    bound_tools: Optional[List[Dict[str, Any]]] = Field(default=None)
    # 当前回合共享的取消信号；不参与模型序列化和复制。
    cancel_event: asyncio.Event | None = Field(default=None, exclude=True)
    # 本次调用需要水合成视觉引用的附件 ID。
    vision_hydrate_attachment_ids: list[int] = Field(default_factory=list)
    # 已水合图片附件的本地二进制路径，key 为附件 ID。
    vision_attachment_binary_paths: dict[int, str] = Field(default_factory=dict)
    # 会话工作区路径，用于解析生成或上传的视觉文件。
    vision_conversation_workspace: str | None = Field(default=None)

    @property
    def _llm_type(self) -> str:
        return "gateway"

    def build_vision_context(self) -> VisionBuildContext:
        """构建传给模型适配器的单次调用视觉上下文。"""
        return build_vision_context(
            hydrate_attachment_ids=self.vision_hydrate_attachment_ids,
            attachment_binary_paths=self.vision_attachment_binary_paths,
            conversation_workspace=self.vision_conversation_workspace,
        )

    def bind_tools(
        self,
        tools: Sequence[Dict[str, Any] | type | BaseTool],
        **kwargs: Any,
    ) -> Runnable[Any, AIMessage]:
        """按 LangChain 契约绑定工具，返回带 ``.bound`` 的 RunnableBinding。

        网关需要 OpenAI function tool 形状的 schema；本地工具可能是
        ``BaseTool``、Pydantic 类型或已序列化 dict，这里统一转换并清洗。

        必须返回 ``self.bind(...)`` 而不是 ``model_copy``：trustcall/langmem
        在 ``enable_deletes`` 且已有记忆时会访问 ``bound.bound.bind_tools``。
        """
        serialized: List[Dict[str, Any]] = []
        for tool in tools:
            if isinstance(tool, dict):
                serialized.append(sanitize_tool_schema(tool))
            else:
                serialized.append(sanitize_tool_schema(convert_to_openai_tool(tool)))
        sanitized: List[Dict[str, Any]] = []
        for item in serialized:
            cleaned = sanitize_tool_schema(item) if isinstance(item, dict) else item
            sanitized.append(cleaned)
            fn = cleaned.get("function") or {}
            params = fn.get("parameters") or {}
            required = params.get("required") or []
            if "tool_call_id" in required:
                logger.warning(
                    "gateway.bind_tools.schema_leak",
                    tool=fn.get("name"),
                    required=required,
                )
            if fn.get("name") in {"read_file", "web_search"}:
                logger.info(
                    "gateway.bind_tools.schema",
                    tool=fn.get("name"),
                    required=required,
                    properties=list((params.get("properties") or {}).keys()),
                )
        bind_kwargs: Dict[str, Any] = {"tools": sanitized}
        tool_choice = kwargs.get("tool_choice")
        if tool_choice is not None:
            bind_kwargs["tool_choice"] = tool_choice
        for key in ("parallel_tool_calls", "strict"):
            if key in kwargs:
                bind_kwargs[key] = kwargs[key]
        return self.bind(**bind_kwargs)

    def _generate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> ChatResult:
        """当前服务只支持异步模型调用，故同步生成入口不实现。"""
        raise NotImplementedError("use async _agenerate")

    def _apply_message_limits(self, messages: List[BaseMessage]) -> List[BaseMessage]:
        """网关请求前对超大 ToolMessage 做物理截断。"""
        return truncate_oversized_tool_messages(list(messages))

    async def _agenerate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> ChatResult:
        """执行一次非流式网关调用，并返回 LangChain 的 ChatResult。

        这个路径主要用于强制最终回答恢复等非流式辅助调用。这里必须保留
        ``tools=[]`` 的语义：即使模型实例上绑定过工具，也要明确告诉适配器本次不传工具。
        """
        messages = self._apply_message_limits(messages)
        adapter = get_adapter(self.spec.family)
        # 不要写成 ``kwargs.get("tools") or self.bound_tools``。
        # 空列表是恢复阶段“禁用工具”的明确信号。
        tools = kwargs["tools"] if "tools" in kwargs else self.bound_tools
        payload = adapter.build_params(
            messages,
            self.spec.gateway_model,
            tools=tools,
            stream=False,
            vision_ctx=self.build_vision_context(),
        )
        try:
            raw = await gateway_client.openai_chat_completion(
                payload,
                timeout_sec=settings.CHAT_GATEWAY_TIMEOUT_SEC,
            )
        except (AppError, GatewayChatError):
            raise
        parsed = adapter.parse_response(raw)
        ai = build_ai_message(
            content=parsed.get("content") or "",
            tool_calls=[
                {
                    "id": call["id"],
                    "name": call["name"],
                    "args": call.get("args") or {},
                }
                for call in parsed.get("tool_calls") or []
            ],
            reasoning=parsed.get("reasoning_content"),
        )
        return ChatResult(generations=[ChatGeneration(message=ai)])

    async def _astream(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[AsyncCallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        """把网关 SSE 流转换成 Agent runner 可消费的 LangChain chunks。

        普通 token chunk 会边到边产出给前端流式显示。流结束时额外产出一个
        ``assembled_step`` chunk，里面带完整助手消息、解析出的工具调用、
        非法工具调用元信息和 finish reason，方便 runner 持久化一个完整模型步骤。
        """
        messages = self._apply_message_limits(messages)
        adapter = get_adapter(self.spec.family)
        # 同样要区分 [] 和 None，恢复调用传 [] 时表示本次完全不启用工具。
        tools = kwargs["tools"] if "tools" in kwargs else self.bound_tools
        payload = adapter.build_params(
            messages,
            self.spec.gateway_model,
            tools=tools,
            stream=True,
            vision_ctx=self.build_vision_context(),
        )
        cancel_event = self.cancel_event
        max_attempts = settings.CHAT_GATEWAY_STEP_RETRIES + 1
        assembled = None
        for attempt in range(1, max_attempts + 1):
            assembler = OpenAIStreamAssembler(thinking=self.spec.thinking)
            streamed_any = False
            try:
                async for line in gateway_client.openai_chat_stream(
                    payload=payload,
                    timeout_sec=settings.CHAT_GATEWAY_TIMEOUT_SEC,
                ):
                    if cancel_event is not None and cancel_event.is_set():
                        break
                    text = line.strip()
                    if not text.startswith("data:"):
                        continue
                    data = text[5:].strip()
                    token_pieces, _chunks = assembler.feed_sse_data(data)
                    for piece in token_pieces:
                        streamed_any = True
                        yield ChatGenerationChunk(
                            message=AIMessageChunk(
                                content=piece.text if piece.lane == "answer" else ""
                            ),
                            generation_info={
                                "token_piece": {"lane": piece.lane, "text": piece.text}
                            },
                        )
                    if data == "[DONE]":
                        break
                candidate = assembler.finish()
                if not _assembled_has_output(candidate):
                    raise GatewayChatError(
                        "gateway_empty_stream",
                        "gateway stream ended without model output",
                    )
                assembled = candidate
                break
            except (AppError, GatewayChatError) as exc:
                if isinstance(exc, AppError):
                    retryable = (
                        exc.code in _RETRYABLE_GATEWAY_APP_CODES
                        and not streamed_any
                        and attempt < max_attempts
                    )
                    error_type = stream_error_class_for_app_error(exc)
                else:
                    retryable = exc.retryable and not streamed_any and attempt < max_attempts
                    error_type = exc.error_type
                logger.warning(
                    "gateway.stream.step_failed",
                    model=self.model_key,
                    attempt=attempt,
                    max_attempts=max_attempts,
                    error_type=error_type,
                    retrying=retryable,
                )
                if not retryable:
                    raise
        if assembled is None:
            raise GatewayChatError(
                "gateway_empty_stream",
                "gateway stream ended without model output",
            )
        assembled_content = assembled.message.content or ""
        assembled_think_content = "".join(
            p.text for p in assembled.token_pieces if p.lane == "think"
        )
        final_message = AIMessageChunk(
            content="",
            tool_calls=list(assembled.message.tool_calls or []),
        )
        yield ChatGenerationChunk(
            message=final_message,
            generation_info={
                "assembled_step": True,
                "assembled_content": assembled_content,
                "assembled_think_content": assembled_think_content,
                "invalid_tool_calls": [item.__dict__ for item in assembled.invalid_tool_calls],
                "finish_reason": assembled.finish_reason,
            },
        )


def _assembled_has_output(assembled: object) -> bool:
    content = str(getattr(assembled.message, "content", "") or "").strip()
    think = "".join(p.text for p in assembled.token_pieces if p.lane == "think").strip()
    tool_calls = list(getattr(assembled.message, "tool_calls", None) or [])
    invalid = list(getattr(assembled, "invalid_tool_calls", None) or [])
    return bool(content or think or tool_calls or invalid)
