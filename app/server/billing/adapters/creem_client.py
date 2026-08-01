from __future__ import annotations

import httpx

from app.server.exceptions.base import AppError
from app.server.exceptions.codes import ErrorCode


class CreemClient:
    """Creem Checkout HTTP 客户端"""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        timeout_seconds: float = 30.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        """注入 API 密钥与可选共享 AsyncClient"""
        self._api_key = api_key.strip()
        self._base_url = base_url.rstrip("/")
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=timeout_seconds)

    async def aclose(self) -> None:
        """关闭自有 HTTP 客户端"""
        if self._owns_client:
            await self._client.aclose()

    async def create_checkout(
        self,
        *,
        product_id: str,
        request_id: str,
        success_url: str,
        customer_email: str | None,
        metadata: dict[str, str],
    ) -> tuple[str, str]:
        """创建一次性 checkout，返回 (checkout_id, checkout_url)"""
        if not self._api_key:
            raise AppError(ErrorCode.SERVICE_UNAVAILABLE, "CREEM_API_KEY is not configured")
        body: dict = {
            "product_id": product_id,
            "request_id": request_id,
            "success_url": success_url,
            "metadata": metadata,
        }
        if customer_email:
            body["customer"] = {"email": customer_email}

        try:
            response = await self._client.post(
                f"{self._base_url}/v1/checkouts",
                headers={
                    "x-api-key": self._api_key,
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "User-Agent": "nexus-studio-backend/creem",
                },
                json=body,
            )
        except httpx.HTTPError as exc:
            raise AppError(ErrorCode.SERVICE_UNAVAILABLE, "creem checkout request failed") from exc

        if response.status_code >= 400:
            detail_body = response.text[:500]
            raise AppError(
                ErrorCode.SERVICE_UNAVAILABLE,
                "creem checkout rejected",
                details={"status_code": response.status_code, "body": detail_body},
            )

        try:
            data = response.json()
        except ValueError as exc:
            raise AppError(ErrorCode.INTERNAL_ERROR, "creem checkout response is not json") from exc

        checkout_id = data.get("id")
        checkout_url = data.get("checkout_url")
        if not isinstance(checkout_id, str) or not checkout_id.strip():
            raise AppError(ErrorCode.INTERNAL_ERROR, "creem checkout missing id")
        if not isinstance(checkout_url, str) or not checkout_url.strip():
            raise AppError(ErrorCode.INTERNAL_ERROR, "creem checkout missing checkout_url")
        return checkout_id.strip(), checkout_url.strip()
