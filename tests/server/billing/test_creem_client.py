from __future__ import annotations

import httpx
import pytest

from app.server.billing.adapters.creem_client import CreemClient
from app.server.exceptions.base import AppError


@pytest.mark.asyncio
async def test_creem_client_create_checkout_maps_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-api-key"] == "creem_test_key"
        assert request.url.path == "/v1/checkouts"
        return httpx.Response(
            200,
            json={
                "id": "ch_abc",
                "checkout_url": "https://checkout.creem.io/ch_abc",
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="https://test-api.creem.io") as http:
        client = CreemClient(
            api_key="creem_test_key",
            base_url="https://test-api.creem.io",
            client=http,
        )
        checkout_id, url = await client.create_checkout(
            product_id="prod_5",
            request_id="req_1",
            success_url="https://app/success",
            customer_email="a@b.com",
            metadata={"user_id": "1"},
        )
    assert checkout_id == "ch_abc"
    assert url.endswith("ch_abc")


@pytest.mark.asyncio
async def test_creem_client_rejects_upstream_error() -> None:
    transport = httpx.MockTransport(lambda _r: httpx.Response(500, json={"error": "x"}))
    async with httpx.AsyncClient(transport=transport, base_url="https://test-api.creem.io") as http:
        client = CreemClient(api_key="k", base_url="https://test-api.creem.io", client=http)
        with pytest.raises(AppError):
            await client.create_checkout(
                product_id="prod_5",
                request_id="req_1",
                success_url="https://app/success",
                customer_email=None,
                metadata={},
            )
