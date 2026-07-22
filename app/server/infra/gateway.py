"""模型网关客户端。"""

from typing import Any, AsyncIterator, Dict, Literal, Optional, TypeVar
from uuid import uuid4

import httpx
from pydantic import BaseModel, ValidationError
from structlog.contextvars import get_contextvars

from app.contracts.gateway import (
    GatewayEnvelopeResponse,
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
from app.server.infra.gateway_errors import GatewayChatError
from app.server.infra.gateway_mapping import app_error_from_gateway_http_status, map_gateway_business_code
from app.server.infra.config import settings
from app.server.infra.embeddings import gateway_embeddings_client
from app.server.infra.gateway_http import gateway_http_client
from app.server.infra.logger import logger
from app.server.infra.gateway_constants import (
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
    GATEWAY_HEADER_TRACE_ID,
    GATEWAY_HEADER_USER_ID,
    GATEWAY_QUERY_REQUEST_ID_TEMPLATE,
    GATEWAY_SYNC_REQUEST_ID_TEMPLATE,
    HTTP_CONTENT_TYPE_JSON,
)
from app.server.exceptions.base import AppError
from app.server.exceptions.codes import ErrorCode
from app.server.assets.schemas.http_schemas import AssetCaptionRequest, AssetCaptionResult, AssetEmbeddingRequest, AssetEmbeddingResult

GatewayResponseModel = TypeVar("GatewayResponseModel", bound=BaseModel)
HttpMethod = Literal["GET", "POST"]


class GatewayClient:
    """模型网关 HTTP 客户端"""

    def __init__(self) -> None:
        self.base_url = settings.GATEWAY_BASE_URL.rstrip("/")
        self._client = gateway_http_client.client

    async def close(self) -> None:
        await gateway_http_client.close()

    def _headers(self, request_id: str) -> Dict[str, str]:
        context = get_contextvars()
        trace_id = context.get("trace_id") or uuid4().hex
        return {
            GATEWAY_HEADER_CONTENT_TYPE: HTTP_CONTENT_TYPE_JSON,
            GATEWAY_HEADER_API_KEY: settings.GATEWAY_API_KEY,
            GATEWAY_HEADER_USER_ID: settings.GATEWAY_USER_ID,
            GATEWAY_HEADER_REQUEST_ID: request_id,
            GATEWAY_HEADER_TRACE_ID: str(trace_id),
        }

    async def _request_json(
        self,
        *,
        method: HttpMethod,
        endpoint: str,
        request_id: str,
        timeout: float,
        transport_error_code: ErrorCode,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        allow_empty: bool = False,
        empty_payload: Any = None,
    ) -> Any:
        """统一传输 / 非 JSON 失败路径；返回解析后的 JSON。"""
        request_headers = headers or self._headers(request_id)
        try:
            response = await self._client.request(
                method,
                f"{self.base_url}{endpoint}",
                headers=request_headers,
                json=json_body,
                params=params,
                timeout=timeout,
            )
        except httpx.TimeoutException as exc:
            logger.warning(
                "gateway.transport_timeout",
                endpoint=endpoint,
                request_id=request_id,
                method=method,
            )
            raise AppError(
                transport_error_code,
                "模型网关请求超时",
                {"request_id": request_id, "endpoint": endpoint},
            ) from exc
        except httpx.RequestError as exc:
            logger.warning(
                "gateway.transport_error",
                endpoint=endpoint,
                request_id=request_id,
                method=method,
                error_type=type(exc).__name__,
            )
            raise AppError(
                transport_error_code,
                "模型网关暂时不可用",
                {"request_id": request_id, "endpoint": endpoint},
            ) from exc

        if allow_empty and not response.content:
            return empty_payload, response.status_code

        try:
            payload = response.json()
        except ValueError as exc:
            logger.warning(
                "gateway.non_json_response",
                endpoint=endpoint,
                request_id=request_id,
                status_code=response.status_code,
            )
            raise AppError(
                ErrorCode.GATEWAY_PROTOCOL_ERROR,
                "模型网关返回了非 JSON 响应",
                {"request_id": request_id, "status_code": response.status_code},
            ) from exc

        if response.status_code >= 400 and not isinstance(payload, dict):
            raise AppError(
                transport_error_code,
                f"模型网关 HTTP {response.status_code}",
                {"request_id": request_id, "status_code": response.status_code},
            )

        return payload, response.status_code

    async def _post_envelope(
        self,
        *,
        endpoint: str,
        request_id: str,
        json_body: dict[str, Any],
        model_type: type[GatewayResponseModel],
        timeout: float,
        headers: dict[str, str] | None = None,
        transport_error_code: ErrorCode = ErrorCode.GATEWAY_SUBMIT_ERROR,
    ) -> GatewayResponseModel:
        payload, _status = await self._request_json(
            method="POST",
            endpoint=endpoint,
            request_id=request_id,
            timeout=timeout,
            transport_error_code=transport_error_code,
            json_body=json_body,
            headers=headers,
        )
        return self._validate_response(payload, model_type, request_id=request_id)

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
        try:
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
                    raise app_error_from_gateway_http_status(
                        response.status_code,
                        request_id=rid,
                        endpoint=endpoint,
                    )
                async for line in response.aiter_lines():
                    if line and line.strip().startswith("data:"):
                        data_frames += 1
                    if line:
                        yield line
        except AppError:
            raise
        except httpx.TimeoutException as exc:
            raise AppError(
                ErrorCode.SERVICE_UNAVAILABLE,
                "模型网关请求超时",
                {"request_id": rid, "endpoint": endpoint},
            ) from exc
        except httpx.RequestError as exc:
            raise AppError(
                ErrorCode.SERVICE_UNAVAILABLE,
                "模型网关暂时不可用",
                {"request_id": rid, "endpoint": endpoint},
            ) from exc
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
        try:
            response = await self._client.post(
                f"{self.base_url}{endpoint}",
                headers=self._headers(rid),
                json=body,
                timeout=timeout,
            )
        except httpx.TimeoutException as exc:
            raise AppError(
                ErrorCode.SERVICE_UNAVAILABLE,
                "模型网关请求超时",
                {"request_id": rid, "endpoint": endpoint},
            ) from exc
        except httpx.RequestError as exc:
            raise AppError(
                ErrorCode.SERVICE_UNAVAILABLE,
                "模型网关暂时不可用",
                {"request_id": rid, "endpoint": endpoint},
            ) from exc
        if response.status_code >= 400:
            logger.error(
                "gateway.openai_chat_completion.error",
                endpoint=endpoint,
                request_id=rid,
                status_code=response.status_code,
                body_preview=response.text[:500],
            )
            raise app_error_from_gateway_http_status(
                response.status_code,
                request_id=rid,
                endpoint=endpoint,
            )
        try:
            payload_json = response.json()
        except ValueError as exc:
            raise AppError(
                ErrorCode.GATEWAY_PROTOCOL_ERROR,
                "模型网关返回了非 JSON 响应",
                {"request_id": rid, "endpoint": endpoint},
            ) from exc
        _raise_on_empty_chat_completion(payload_json, request_id=rid)
        return payload_json

    async def list_openai_models(self) -> Dict[str, Any]:
        """调用网关 OpenAI 兼容模型列表接口。"""

        endpoint = "/api/v1/models"
        rid = GATEWAY_SYNC_REQUEST_ID_TEMPLATE.format(request_uuid=uuid4().hex)
        logger.info("gateway.list_openai_models", endpoint=endpoint, request_id=rid)
        payload, _status = await self._request_json(
            method="GET",
            endpoint=endpoint,
            request_id=rid,
            timeout=settings.GATEWAY_TIMEOUTS.query_seconds,
            transport_error_code=ErrorCode.GENERATION_MODEL_LIST_UNAVAILABLE,
        )
        if not isinstance(payload, dict) or not payload:
            logger.error("gateway.list_openai_models.empty_body", endpoint=endpoint, request_id=rid)
            raise AppError(
                ErrorCode.GENERATION_MODEL_LIST_UNAVAILABLE,
                "模型列表暂时不可用",
                {"request_id": rid},
            )
        return payload

    async def list_generation_models(self) -> list[GatewayModelItem]:
        """读取并校验网关模型目录。"""

        payload = await self.list_openai_models()
        return self._validate_response(payload, GatewayModelsResponse).data

    async def submit_image(
        self,
        payload: GatewayImageSubmitRequest,
        request_id: str,
    ) -> GatewayTaskSubmitResponse:
        endpoint = GATEWAY_ENDPOINT_IMAGE_GEN
        logger.info("gateway.submit_image", endpoint=endpoint, request_id=request_id)
        return await self._post_envelope(
            endpoint=endpoint,
            request_id=request_id,
            json_body=payload.model_dump(mode="json", by_alias=True, exclude_none=True),
            model_type=GatewayTaskSubmitResponse,
            timeout=settings.GATEWAY_TIMEOUTS.generation_seconds,
        )

    async def submit_video(
        self,
        payload: GatewayVideoSubmitRequest,
        request_id: str,
    ) -> GatewayTaskSubmitResponse:
        endpoint = GATEWAY_ENDPOINT_VIDEO_GEN
        logger.info("gateway.submit_video", endpoint=endpoint, request_id=request_id)
        return await self._post_envelope(
            endpoint=endpoint,
            request_id=request_id,
            json_body=payload.model_dump(mode="json", by_alias=True, exclude_none=True),
            model_type=GatewayTaskSubmitResponse,
            timeout=settings.GATEWAY_TIMEOUTS.generation_seconds,
        )

    async def submit_tts(
        self,
        payload: GatewayTTSSubmitRequest,
        request_id: str,
    ) -> GatewayTaskSubmitResponse:
        endpoint = GATEWAY_ENDPOINT_TTS
        logger.info("gateway.submit_tts", endpoint=endpoint, request_id=request_id)
        return await self._post_envelope(
            endpoint=endpoint,
            request_id=request_id,
            json_body=payload.model_dump(mode="json", by_alias=True, exclude_none=True),
            model_type=GatewayTaskSubmitResponse,
            timeout=settings.GATEWAY_TIMEOUTS.generation_seconds,
        )

    async def list_voices(self, model: str, request_id: str | None = None) -> GatewayListVoicesResponse:
        rid = request_id or GATEWAY_SYNC_REQUEST_ID_TEMPLATE.format(request_uuid=uuid4().hex)
        logger.info("gateway.list_voices", endpoint=GATEWAY_ENDPOINT_VOICES, model=model, request_id=rid)
        payload, _status = await self._request_json(
            method="GET",
            endpoint=GATEWAY_ENDPOINT_VOICES,
            request_id=rid,
            timeout=settings.GATEWAY_TIMEOUTS.query_seconds,
            transport_error_code=ErrorCode.GENERATION_MODEL_LIST_UNAVAILABLE,
            params={"model": model},
        )
        return self._validate_response(payload, GatewayListVoicesResponse, request_id=rid)

    async def get_task(
        self,
        task_id: int,
        request_id: Optional[str] = None,
    ) -> GatewayTaskStatusResponse:
        endpoint = GATEWAY_ENDPOINT_TASK_GET
        rid = request_id or GATEWAY_QUERY_REQUEST_ID_TEMPLATE.format(task_id=task_id, request_uuid=uuid4().hex)
        logger.info("gateway.get_task", endpoint=endpoint, task_id=task_id)
        return await self._post_envelope(
            endpoint=endpoint,
            request_id=rid,
            json_body={"taskId": task_id},
            model_type=GatewayTaskStatusResponse,
            timeout=settings.GATEWAY_TIMEOUTS.query_seconds,
            transport_error_code=ErrorCode.GENERATION_STATUS_UNAVAILABLE,
        )

    async def get_tasks_queue(self, task_ids: list[int]) -> GatewayQueueResponse:
        endpoint = GATEWAY_ENDPOINT_TASKS_QUEUE
        rid = GATEWAY_SYNC_REQUEST_ID_TEMPLATE.format(request_uuid=uuid4().hex)
        logger.info("gateway.get_tasks_queue", endpoint=endpoint, task_ids=task_ids)
        return await self._post_envelope(
            endpoint=endpoint,
            request_id=rid,
            json_body={"taskIds": task_ids},
            model_type=GatewayQueueResponse,
            timeout=settings.GATEWAY_TIMEOUTS.query_seconds,
            transport_error_code=ErrorCode.GENERATION_QUEUE_UNAVAILABLE,
        )

    async def cancel_task(self, task_id: int, user_id: str) -> None:
        endpoint = GATEWAY_ENDPOINT_TASK_CANCEL
        rid = GATEWAY_QUERY_REQUEST_ID_TEMPLATE.format(task_id=task_id, request_uuid=uuid4().hex)
        logger.info("gateway.cancel_task", endpoint=endpoint, task_id=task_id)
        headers = {**self._headers(rid), GATEWAY_HEADER_USER_ID: str(user_id)}
        payload, status_code = await self._request_json(
            method="POST",
            endpoint=endpoint,
            request_id=rid,
            timeout=settings.GATEWAY_TIMEOUTS.query_seconds,
            transport_error_code=ErrorCode.TASK_CANCEL_FAILED,
            json_body={"taskId": task_id},
            headers=headers,
            allow_empty=True,
            empty_payload={"code": 0, "message": "Success"},
        )
        if isinstance(payload, dict) and payload.get("code", 0) != 0:
            raise map_gateway_business_code(
                int(payload.get("code") or 0),
                payload.get("message"),
                request_id=rid,
            )
        if status_code >= 400:
            raise AppError(
                ErrorCode.TASK_CANCEL_FAILED,
                "上游任务取消失败，请稍后重试",
                {"request_id": rid, "status_code": status_code},
            )

    async def caption_asset(self, request: AssetCaptionRequest) -> AssetCaptionResult:
        """调用网关生成资产 caption，接口由网关实现"""

        endpoint = GATEWAY_ENDPOINT_CAPTION_SYNC
        rid = GATEWAY_SYNC_REQUEST_ID_TEMPLATE.format(request_uuid=uuid4().hex)
        logger.info("gateway.caption_asset", endpoint=endpoint, storage_key=request.storage_key)
        payload, _status = await self._request_json(
            method="POST",
            endpoint=endpoint,
            request_id=rid,
            timeout=settings.GATEWAY_TIMEOUTS.generation_seconds,
            transport_error_code=ErrorCode.SERVICE_UNAVAILABLE,
            json_body=request.model_dump(mode="json"),
        )
        try:
            return AssetCaptionResult.model_validate(payload)
        except ValidationError as exc:
            logger.error(
                "gateway.caption_asset.protocol_error",
                endpoint=endpoint,
                request_id=rid,
                response_summary=safe_gateway_response_summary(payload),
            )
            raise AppError(
                ErrorCode.GATEWAY_PROTOCOL_ERROR,
                "模型网关返回了不符合 caption 协议的数据",
                {"response_model": AssetCaptionResult.__name__},
            ) from exc

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
        *,
        request_id: str | None = None,
    ) -> GatewayResponseModel:
        try:
            parsed = model_type.model_validate(payload)
        except ValidationError as exc:
            logger.warning(
                "gateway.protocol.invalid_response",
                response_model=model_type.__name__,
                request_id=request_id,
                response_summary=safe_gateway_response_summary(payload),
                validation_errors=exc.error_count(),
            )
            raise AppError(
                ErrorCode.GATEWAY_PROTOCOL_ERROR,
                "模型网关返回了不符合协议的数据",
                {"response_model": model_type.__name__, "request_id": request_id},
            ) from exc

        if not isinstance(parsed, GatewayEnvelopeResponse):
            return parsed
        if parsed.code != 0:
            logger.warning(
                "gateway.business_error",
                gateway_code=parsed.code,
                request_id=request_id,
                response_model=model_type.__name__,
            )
            raise map_gateway_business_code(
                int(parsed.code),
                parsed.message,
                request_id=request_id,
                response_model=model_type.__name__,
            )
        return parsed


gateway_client = GatewayClient()


def _raise_on_empty_chat_completion(payload: Dict[str, Any], *, request_id: str) -> None:
    if not isinstance(payload, dict):
        raise AppError(
            ErrorCode.GATEWAY_PROTOCOL_ERROR,
            "模型网关 completion 不是对象",
            {"request_id": request_id},
        )
    choices = payload.get("choices")
    if not isinstance(choices, list) or len(choices) == 0:
        logger.error(
            "gateway.openai_chat_completion.empty_choices",
            request_id=request_id,
        )
        raise AppError(
            ErrorCode.GATEWAY_PROTOCOL_ERROR,
            "模型网关 completion 缺少 choices",
            {"request_id": request_id},
        )
