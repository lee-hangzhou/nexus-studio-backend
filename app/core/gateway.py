"""模型网关客户端。"""

from typing import Any, AsyncIterator, Dict, Optional, TypeVar
from uuid import uuid4

from pydantic import BaseModel, ValidationError

from app.contracts.gateway import (
    GatewayImageSubmitRequest,
    GatewayModelItem,
    GatewayModelsResponse,
    GatewayQueueResponse,
    GatewayTaskStatusResponse,
    GatewayTaskSubmitResponse,
    GatewayTTSSubmitRequest,
    GatewayVideoSubmitRequest,
    GatewayListVoicesResponse,
    safe_gateway_response_summary,
)
from app.core.gateway_errors import GatewayChatError, gateway_error_from_http_status
from app.core.config import settings
from app.core.embeddings import gateway_embeddings_client
from app.core.gateway_http import gateway_http_client
from app.core.logger import logger
from app.domain.constants import (
    GATEWAY_ENDPOINT_CAPTION_SYNC,
    GATEWAY_ENDPOINT_IMAGE_GEN,
    GATEWAY_ENDPOINT_TASK_CANCEL,
    GATEWAY_ENDPOINT_TASK_GET,
    GATEWAY_ENDPOINT_TASKS_QUEUE,
    GATEWAY_ENDPOINT_TTS,
    GATEWAY_ENDPOINT_VIDEO_GEN,
    GATEWAY_ENDPOINT_VOICES,
    GATEWAY_HEADER_API_KEY,
    GATEWAY_HEADER_CONTENT_TYPE,
    GATEWAY_HEADER_REQUEST_ID,
    GATEWAY_HEADER_USER_ID,
    GATEWAY_QUERY_REQUEST_ID_TEMPLATE,
    GATEWAY_RESPONSE_DATA_KEY,
    GATEWAY_RESPONSE_RESULT_KEY,
    GATEWAY_SYNC_REQUEST_ID_TEMPLATE,
    HTTP_CONTENT_TYPE_JSON,
)
from app.exceptions.base import AppError
from app.exceptions.codes import ErrorCode
from app.schemas.asset import AssetCaptionRequest, AssetCaptionResult, AssetEmbeddingRequest, AssetEmbeddingResult

GatewayResponseModel = TypeVar("GatewayResponseModel", bound=BaseModel)


class GatewayClient:
    """模型网关 HTTP 客户端"""

    def __init__(self) -> None:
        self.base_url = settings.GATEWAY_BASE_URL.rstrip("/")
        self._client = gateway_http_client.client

    async def close(self) -> None:
        """关闭复用的 HTTP 连接池。"""

        await gateway_http_client.close()

    def _headers(self, request_id: str) -> Dict[str, str]:
        """构造模型网关要求的鉴权与幂等请求头"""

        return {
            GATEWAY_HEADER_CONTENT_TYPE: HTTP_CONTENT_TYPE_JSON,
            GATEWAY_HEADER_API_KEY: settings.GATEWAY_API_KEY,
            GATEWAY_HEADER_USER_ID: settings.GATEWAY_USER_ID,
            GATEWAY_HEADER_REQUEST_ID: request_id,
        }

    async def openai_chat_stream(
        self,
        payload: Dict[str, Any],
        request_id: Optional[str] = None,
        timeout_sec: Optional[float] = None,
    ) -> AsyncIterator[str]:
        """调用网关 OpenAI 兼容流式 chat/completions 接口。"""

        endpoint = "/api/v1/chat/completions"
        rid = request_id or GATEWAY_SYNC_REQUEST_ID_TEMPLATE.format(request_uuid=uuid4().hex)
        timeout = (
            timeout_sec
            if timeout_sec is not None
            else settings.GATEWAY_TIMEOUTS.read_seconds
        )
        logger.info("gateway.openai_chat_stream", endpoint=endpoint, request_id=rid)
        data_frames = 0
        async with self._client.stream(
            "POST",
            f"{self.base_url}{endpoint}",
            headers=self._headers(rid),
            json=payload,
            timeout=timeout,
        ) as response:
            if response.status_code >= 400:
                body = await response.aread()
                logger.error(
                    "gateway.openai_chat_stream.error",
                    endpoint=endpoint,
                    request_id=rid,
                    status_code=response.status_code,
                    body_preview=body[:500],
                )
                raise gateway_error_from_http_status(response.status_code, body)
            async for line in response.aiter_lines():
                if line and line.strip().startswith("data:"):
                    data_frames += 1
                if line:
                    yield line
        if data_frames == 0:
            logger.error(
                "gateway.openai_chat_stream.empty",
                endpoint=endpoint,
                request_id=rid,
            )
            raise GatewayChatError(
                "gateway_empty_stream",
                "gateway stream contained no SSE data frames",
            )

    async def openai_chat_completion(
        self,
        payload: Dict[str, Any],
        request_id: Optional[str] = None,
        timeout_sec: Optional[float] = None,
    ) -> Dict[str, Any]:
        """调用网关 OpenAI 兼容非流式 chat/completions 接口。"""

        endpoint = "/api/v1/chat/completions"
        rid = request_id or GATEWAY_SYNC_REQUEST_ID_TEMPLATE.format(request_uuid=uuid4().hex)
        timeout = (
            timeout_sec
            if timeout_sec is not None
            else settings.GATEWAY_TIMEOUTS.read_seconds
        )
        body = {**payload, "stream": False}
        logger.info("gateway.openai_chat_completion", endpoint=endpoint, request_id=rid)
        response = await self._client.post(
            f"{self.base_url}{endpoint}",
            headers=self._headers(rid),
            json=body,
            timeout=timeout,
        )
        if response.status_code >= 400:
            logger.error(
                "gateway.openai_chat_completion.error",
                endpoint=endpoint,
                request_id=rid,
                status_code=response.status_code,
                body_preview=response.text[:500],
            )
            raise gateway_error_from_http_status(response.status_code, response.content)
        payload_json = response.json()
        _raise_on_empty_chat_completion(payload_json, request_id=rid)
        return payload_json

    async def list_openai_models(self) -> Dict[str, Any]:
        """调用网关 OpenAI 兼容模型列表接口。"""

        endpoint = "/api/v1/models"
        rid = GATEWAY_SYNC_REQUEST_ID_TEMPLATE.format(request_uuid=uuid4().hex)
        logger.info("gateway.list_openai_models", endpoint=endpoint, request_id=rid)
        response = await self._client.get(
            f"{self.base_url}{endpoint}",
            headers=self._headers(rid),
            timeout=settings.GATEWAY_TIMEOUTS.query_seconds,
        )
        response.raise_for_status()
        body = response.text.strip()
        if not body:
            logger.error("gateway.list_openai_models.empty_body", endpoint=endpoint, request_id=rid)
            raise ValueError("gateway /api/v1/models returned empty body")
        try:
            return response.json()
        except ValueError as exc:
            logger.error(
                "gateway.list_openai_models.non_json",
                endpoint=endpoint,
                request_id=rid,
                body_preview=body[:200],
            )
            raise ValueError("gateway /api/v1/models returned non-json response") from exc

    async def list_generation_models(self) -> list[GatewayModelItem]:
        """读取并校验网关模型目录。"""

        payload = await self.list_openai_models()
        return self._validate_response(payload, GatewayModelsResponse).data

    async def submit_image(
        self,
        payload: GatewayImageSubmitRequest,
        request_id: str,
    ) -> GatewayTaskSubmitResponse:
        """提交图片生成异步任务"""

        endpoint = GATEWAY_ENDPOINT_IMAGE_GEN
        logger.info("gateway.submit_image", endpoint=endpoint, request_id=request_id)
        response = await self._client.post(
            f"{self.base_url}{endpoint}",
            headers=self._headers(request_id),
            json=payload.model_dump(mode="json", by_alias=True, exclude_none=True),
            timeout=settings.GATEWAY_TIMEOUTS.generation_seconds,
        )
        response.raise_for_status()
        return self._validate_response(response.json(), GatewayTaskSubmitResponse)

    async def submit_video(
        self,
        payload: GatewayVideoSubmitRequest,
        request_id: str,
    ) -> GatewayTaskSubmitResponse:
        """提交视频生成异步任务"""

        endpoint = GATEWAY_ENDPOINT_VIDEO_GEN
        logger.info("gateway.submit_video", endpoint=endpoint, request_id=request_id)
        response = await self._client.post(
            f"{self.base_url}{endpoint}",
            headers=self._headers(request_id),
            json=payload.model_dump(mode="json", by_alias=True, exclude_none=True),
            timeout=settings.GATEWAY_TIMEOUTS.generation_seconds,
        )
        response.raise_for_status()
        return self._validate_response(response.json(), GatewayTaskSubmitResponse)

    async def submit_tts(
        self,
        payload: GatewayTTSSubmitRequest,
        request_id: str,
    ) -> GatewayTaskSubmitResponse:
        """提交语音合成异步任务"""

        endpoint = GATEWAY_ENDPOINT_TTS
        logger.info("gateway.submit_tts", endpoint=endpoint, request_id=request_id)
        response = await self._client.post(
            f"{self.base_url}{endpoint}",
            headers=self._headers(request_id),
            json=payload.model_dump(mode="json", by_alias=True, exclude_none=True),
            timeout=settings.GATEWAY_TIMEOUTS.generation_seconds,
        )
        response.raise_for_status()
        return self._validate_response(response.json(), GatewayTaskSubmitResponse)

    async def list_voices(self, model: str, request_id: str | None = None) -> GatewayListVoicesResponse:
        """查询 TTS 模型可用音色列表"""

        rid = request_id or GATEWAY_SYNC_REQUEST_ID_TEMPLATE.format(request_uuid=uuid4().hex)
        logger.info("gateway.list_voices", endpoint=GATEWAY_ENDPOINT_VOICES, model=model, request_id=rid)
        response = await self._client.get(
            f"{self.base_url}{GATEWAY_ENDPOINT_VOICES}",
            headers=self._headers(rid),
            params={"model": model},
            timeout=settings.GATEWAY_TIMEOUTS.query_seconds,
        )
        response.raise_for_status()
        return self._validate_response(response.json(), GatewayListVoicesResponse)

    async def get_task(
        self,
        task_id: int,
        request_id: Optional[str] = None,
    ) -> GatewayTaskStatusResponse:
        """查询网关异步任务状态"""

        endpoint = GATEWAY_ENDPOINT_TASK_GET
        rid = request_id or GATEWAY_QUERY_REQUEST_ID_TEMPLATE.format(task_id=task_id, request_uuid=uuid4().hex)
        logger.info("gateway.get_task", endpoint=endpoint, task_id=task_id)
        response = await self._client.post(
            f"{self.base_url}{endpoint}",
            headers=self._headers(rid),
            json={"taskId": task_id},
            timeout=settings.GATEWAY_TIMEOUTS.query_seconds,
        )
        response.raise_for_status()
        return self._validate_response(response.json(), GatewayTaskStatusResponse)

    async def get_tasks_queue(self, task_ids: list[int]) -> GatewayQueueResponse:
        """批量查询并校验网关任务排队情况。"""

        endpoint = GATEWAY_ENDPOINT_TASKS_QUEUE
        rid = GATEWAY_SYNC_REQUEST_ID_TEMPLATE.format(request_uuid=uuid4().hex)
        logger.info("gateway.get_tasks_queue", endpoint=endpoint, task_ids=task_ids)
        response = await self._client.post(
            f"{self.base_url}{endpoint}",
            headers=self._headers(rid),
            json={"taskIds": task_ids},
            timeout=settings.GATEWAY_TIMEOUTS.query_seconds,
        )
        response.raise_for_status()
        return self._validate_response(response.json(), GatewayQueueResponse)

    async def cancel_task(self, task_id: int, user_id: str) -> None:
        """取消网关异步任务，上游失败时抛出异常"""

        endpoint = GATEWAY_ENDPOINT_TASK_CANCEL
        rid = GATEWAY_QUERY_REQUEST_ID_TEMPLATE.format(task_id=task_id, request_uuid=uuid4().hex)
        logger.info("gateway.cancel_task", endpoint=endpoint, task_id=task_id)
        headers = {**self._headers(rid), GATEWAY_HEADER_USER_ID: str(user_id)}
        response = await self._client.post(
            f"{self.base_url}{endpoint}",
            headers=headers,
            json={"taskId": task_id},
            timeout=settings.GATEWAY_TIMEOUTS.query_seconds,
        )
        response.raise_for_status()

    async def caption_asset(self, request: AssetCaptionRequest) -> AssetCaptionResult:
        """调用网关生成资产 caption，接口由网关实现"""

        endpoint = GATEWAY_ENDPOINT_CAPTION_SYNC
        rid = GATEWAY_SYNC_REQUEST_ID_TEMPLATE.format(request_uuid=uuid4().hex)
        logger.info("gateway.caption_asset", endpoint=endpoint, storage_key=request.storage_key)
        response = await self._client.post(
            f"{self.base_url}{endpoint}",
            headers=self._headers(rid),
            json=request.model_dump(mode="json"),
            timeout=settings.GATEWAY_TIMEOUTS.generation_seconds,
        )
        response.raise_for_status()
        return AssetCaptionResult.model_validate(extract_gateway_result(response.json()))

    async def embed_asset(self, request: AssetEmbeddingRequest) -> AssetEmbeddingResult:
        """调用 OpenAI 兼容 Embeddings 接口生成多模态向量。"""

        return await gateway_embeddings_client.embed_asset(request)

    async def embed_texts_batch(self, model: str, texts: list[str]) -> list[list[float]]:
        """批量文本 embedding（OpenAI 兼容 /api/v1/embeddings）。"""

        return await gateway_embeddings_client.embed_texts_batch(texts, model=model)

    @staticmethod
    def _validate_response(
        payload: Any,
        model_type: type[GatewayResponseModel],
    ) -> GatewayResponseModel:
        """在网关边界校验响应，禁止原始字典泄漏到生成业务层。"""

        try:
            parsed = model_type.model_validate(payload)
        except ValidationError as exc:
            logger.error(
                "gateway.protocol.invalid_response",
                response_model=model_type.__name__,
                response_summary=safe_gateway_response_summary(payload),
                validation_errors=exc.error_count(),
            )
            raise AppError(
                ErrorCode.GATEWAY_PROTOCOL_ERROR,
                "模型网关返回了不符合协议的数据",
                {"response_model": model_type.__name__},
            ) from exc

        code = getattr(parsed, "code", 0)
        if code != 0:
            message = getattr(parsed, "message", None) or "gateway returned non-zero code"
            raise AppError(
                ErrorCode.GATEWAY_PROTOCOL_ERROR,
                message,
                {"response_model": model_type.__name__, "code": code},
            )
        return parsed


gateway_client = GatewayClient()


def _raise_on_empty_chat_completion(payload: Dict[str, Any], *, request_id: str) -> None:
    code = payload.get("code")
    if code not in (None, 0, "0", 200, "200"):
        message = payload.get("message") or payload.get("error") or payload
        logger.error(
            "gateway.openai_chat_completion.envelope_error",
            request_id=request_id,
            code=code,
            message=str(message)[:500],
        )
        raise GatewayChatError(
            "gateway_upstream_failed",
            str(message),
            retryable=False,
        )
    body = payload
    if "choices" not in body:
        data = body.get(GATEWAY_RESPONSE_DATA_KEY)
        if isinstance(data, dict):
            body = data
    choices = body.get("choices") if isinstance(body, dict) else None
    if not choices:
        logger.error(
            "gateway.openai_chat_completion.empty_choices",
            request_id=request_id,
        )
        raise GatewayChatError(
            "gateway_empty_stream",
            "gateway completion returned no choices",
        )


def extract_gateway_result(response: Dict[str, Any]) -> Any:
    """从模型网关响应信封中取出业务结果"""

    envelope = response.get(GATEWAY_RESPONSE_DATA_KEY, response)
    if isinstance(envelope, dict):
        return envelope.get(GATEWAY_RESPONSE_RESULT_KEY, envelope)
    return envelope
