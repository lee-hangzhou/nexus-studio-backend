"""OpenAI 兼容 Embeddings API 客户端（/api/v1/embeddings）。

鉴权与 Chat 相同：复用 settings.GATEWAY_BASE_URL、GATEWAY_API_KEY、GATEWAY_USER_ID。
"""

from __future__ import annotations

import base64
import math
import time
from typing import Any, List, Sequence, Union
from uuid import uuid4

import httpx

from app.core.config import settings
from app.core.gateway_http import gateway_http_client
from app.core.logger import logger
from app.core.object_storage import object_storage
from app.domain.constants import (
    GATEWAY_ENDPOINT_OPENAI_EMBEDDINGS,
    GATEWAY_HEADER_API_KEY,
    GATEWAY_HEADER_CONTENT_TYPE,
    GATEWAY_HEADER_REQUEST_ID,
    GATEWAY_HEADER_USER_ID,
    HTTP_CONTENT_TYPE_JSON,
)
from app.schemas.asset import AssetEmbeddingRequest, AssetEmbeddingResult

EmbeddingInput = Union[str, List[str], List[dict[str, Any]]]


class GatewayEmbeddingError(Exception):
    """网关 Embeddings 接口返回的业务错误。"""

    def __init__(self, message: str, *, error_type: str | None = None, status_code: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.error_type = error_type
        self.status_code = status_code


class GatewayEmbeddingsClient:
    """调用 vlm-gateway OpenAI 兼容 Embeddings 接口。"""

    def __init__(self) -> None:
        self.base_url = settings.GATEWAY_BASE_URL.rstrip("/")

    def _headers(self, request_id: str) -> dict[str, str]:
        return {
            GATEWAY_HEADER_CONTENT_TYPE: HTTP_CONTENT_TYPE_JSON,
            GATEWAY_HEADER_API_KEY: settings.GATEWAY_API_KEY,
            GATEWAY_HEADER_USER_ID: settings.GATEWAY_USER_ID,
            GATEWAY_HEADER_REQUEST_ID: request_id,
        }

    @staticmethod
    def _parse_vectors(
        body: dict[str, Any],
        *,
        expected_count: int,
        expected_dimensions: int | None,
    ) -> list[list[float]]:
        data = body.get("data")
        if not isinstance(data, list) or len(data) != expected_count:
            raise GatewayEmbeddingError("embeddings response missing data list", error_type="invalid_response")
        indexed: dict[int, list[float]] = {}
        for item in data:
            if not isinstance(item, dict) or set(item) != {"object", "embedding", "index"}:
                raise GatewayEmbeddingError("embedding item violates response contract", error_type="invalid_response")
            embedding = item.get("embedding")
            index = item.get("index")
            if item.get("object") != "embedding" or not isinstance(index, int) or isinstance(index, bool):
                raise GatewayEmbeddingError("embedding item has invalid object or index", error_type="invalid_response")
            if index in indexed or index < 0 or index >= expected_count:
                raise GatewayEmbeddingError("embedding indexes are not a complete sequence", error_type="invalid_response")
            if not isinstance(embedding, list) or not embedding:
                raise GatewayEmbeddingError("embedding vector is missing or empty", error_type="invalid_response")
            vector: list[float] = []
            for value in embedding:
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                    raise GatewayEmbeddingError("embedding vector contains a non-number", error_type="invalid_response")
                vector.append(float(value))
            if expected_dimensions is not None and len(vector) != expected_dimensions:
                raise GatewayEmbeddingError("embedding vector dimension mismatch", error_type="invalid_response")
            indexed[index] = vector
        if set(indexed) != set(range(expected_count)):
            raise GatewayEmbeddingError("embedding indexes are not a complete sequence", error_type="invalid_response")
        dimensions = {len(vector) for vector in indexed.values()}
        if len(dimensions) != 1:
            raise GatewayEmbeddingError("embedding vectors have inconsistent dimensions", error_type="invalid_response")
        return [indexed[index] for index in range(expected_count)]

    @staticmethod
    def _raise_for_error_response(response: httpx.Response) -> None:
        try:
            body = response.json()
        except ValueError:
            body = {}
        if isinstance(body, dict) and isinstance(body.get("error"), dict):
            err = body["error"]
            message = str(err.get("message") or response.text or "embedding request failed")
            error_type = err.get("type")
            if isinstance(error_type, str):
                raise GatewayEmbeddingError(message, error_type=error_type, status_code=response.status_code)
            raise GatewayEmbeddingError(message, status_code=response.status_code)
        response.raise_for_status()

    async def create(
        self,
        *,
        model: str,
        input: EmbeddingInput,
        request_id: str | None = None,
        encoding_format: str | None = None,
        dimensions: int | None = None,
        timeout_sec: float | None = None,
    ) -> list[list[float]]:
        """POST /api/v1/embeddings，返回按 index 排序的向量列表。"""

        rid = request_id or str(uuid4())
        payload: dict[str, Any] = {
            "model": model,
            "input": input,
            "encoding_format": encoding_format or settings.GATEWAY_EMBEDDING_ENCODING_FORMAT,
        }
        if dimensions is not None:
            payload["dimensions"] = dimensions
        timeout = (
            timeout_sec
            if timeout_sec is not None
            else settings.GATEWAY_TIMEOUTS.embedding_seconds
        )
        endpoint = GATEWAY_ENDPOINT_OPENAI_EMBEDDINGS
        started = time.perf_counter()
        logger.info("gateway.embeddings.create", endpoint=endpoint, model=model, request_id=rid)
        response = await gateway_http_client.client.post(
            f"{self.base_url}{endpoint}",
            headers=self._headers(rid),
            json=payload,
            timeout=timeout,
        )
        if response.status_code >= 400:
            self._raise_for_error_response(response)
        body = response.json()
        if not isinstance(body, dict):
            raise GatewayEmbeddingError("embeddings response is not json object", error_type="invalid_response")
        expected_count = 1 if isinstance(input, str) or (input and isinstance(input[0], dict)) else len(input)
        vectors = self._parse_vectors(
            body,
            expected_count=expected_count,
            expected_dimensions=dimensions,
        )
        logger.info(
            "gateway.embeddings.done",
            endpoint=endpoint,
            model=model,
            request_id=rid,
            duration_ms=round((time.perf_counter() - started) * 1000, 2),
            vector_count=len(vectors),
        )
        return vectors

    async def embed_texts_batch(
        self,
        texts: Sequence[str],
        *,
        model: str | None = None,
        request_id: str | None = None,
        dimensions: int | None = None,
        timeout_sec: float | None = None,
    ) -> list[list[float]]:
        if not texts:
            return []
        resolved_model = model or settings.GATEWAY_TEXT_EMBEDDING_MODEL
        resolved_dimensions = (
            dimensions if dimensions is not None else settings.ASSET_VECTOR_DIMENSION
        )
        return await self.create(
            model=resolved_model,
            input=list(texts),
            request_id=request_id,
            dimensions=resolved_dimensions,
            timeout_sec=timeout_sec,
        )

    async def embed_text(
        self,
        text: str,
        *,
        model: str | None = None,
        request_id: str | None = None,
    ) -> list[float]:
        vectors = await self.embed_texts_batch([text], model=model, request_id=request_id)
        if len(vectors) != 1:
            raise GatewayEmbeddingError("single embedding request returned multiple vectors", error_type="invalid_response")
        return vectors[0]

    @staticmethod
    def build_multimodal_input(
        text: str,
        *,
        image_bytes: bytes | None = None,
        mime_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """按 OpenRouter 多模态格式构造 input。"""

        parts: list[dict[str, Any]] = [{"type": "text", "text": text}]
        if image_bytes is not None and mime_type:
            encoded = base64.b64encode(image_bytes).decode("ascii")
            parts.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime_type};base64,{encoded}"},
                }
            )
        return parts

    async def embed_asset(self, request: AssetEmbeddingRequest) -> AssetEmbeddingResult:
        model = request.model or settings.GATEWAY_MULTIMODAL_EMBEDDING_MODEL
        image_bytes: bytes | None = None
        if request.storage_key and request.mime_type and request.mime_type.startswith("image/"):
            image_bytes = await object_storage.get_object_bytes(request.storage_key)
        if image_bytes is None:
            embedding_input: EmbeddingInput = request.text
        else:
            embedding_input = self.build_multimodal_input(
                request.text,
                image_bytes=image_bytes,
                mime_type=request.mime_type,
            )
        vectors = await self.create(model=model, input=embedding_input)
        if len(vectors) != 1:
            raise GatewayEmbeddingError("asset embedding returned multiple vectors", error_type="invalid_response")
        return AssetEmbeddingResult(embedding=vectors[0])


gateway_embeddings_client = GatewayEmbeddingsClient()
