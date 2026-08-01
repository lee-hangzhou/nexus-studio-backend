from __future__ import annotations

import json

from fastapi import APIRouter, Request

from app.server.api.schemas import Response
from app.server.auth.services.registry import registry
from app.server.billing.composition import (
    get_checkout_service,
    get_user_credit_balance,
    get_webhook_service,
    list_public_packs,
)
from app.server.billing.domain.packs import CREDITS_PER_USD
from app.server.billing.schemas.http import (
    CreateCheckoutRequest,
    CreateCheckoutResponse,
    CreditBalanceResponse,
    CreditPackListResponse,
    WebhookApplyResponse,
)
from app.server.exceptions.base import AppError
from app.server.exceptions.codes import ErrorCode
from app.server.infra.config import settings

router = APIRouter()


@router.get("/packs")
async def list_packs() -> Response[CreditPackListResponse]:
    """公开列出一次性积分包"""
    return Response(
        data=CreditPackListResponse(
            packs=list_public_packs(),
            credits_per_usd=CREDITS_PER_USD,
        )
    )


@router.get("/balance")
async def get_balance(request: Request) -> Response[CreditBalanceResponse]:
    """查询当前登录用户积分余额"""
    user_id: int = request.state.user_id
    balance = await get_user_credit_balance(user_id)
    return Response(data=CreditBalanceResponse(balance=balance))


@router.post("/checkout")
async def create_checkout(
    request: Request,
    body: CreateCheckoutRequest,
) -> Response[CreateCheckoutResponse]:
    """为当前用户创建 Creem 一次性支付会话"""
    if not settings.CREEM_API_KEY.strip():
        raise AppError(ErrorCode.SERVICE_UNAVAILABLE, "billing provider is not configured")

    user_id: int = request.state.user_id
    user = await registry.auth_service.get_current_user(user_id)
    service = get_checkout_service()
    session = await service.create_checkout(
        user_id=user_id,
        pack_key=body.pack_key,
        customer_email=user.email,
    )
    return Response(
        data=CreateCheckoutResponse(
            order_id=session.order_id,
            request_id=session.request_id,
            pack_key=session.pack_key,
            credits=session.credits,
            price_usd_cents=session.price_usd_cents,
            checkout_url=session.checkout_url,
        )
    )


@router.post("/webhook")
async def creem_webhook(request: Request) -> Response[WebhookApplyResponse]:
    """接收 Creem webhook 并幂等入账"""
    raw = await request.body()
    signature = request.headers.get("creem-signature")
    if not settings.CREEM_WEBHOOK_SECRET.strip():
        raise AppError(ErrorCode.SERVICE_UNAVAILABLE, "creem webhook secret is not configured")

    service = get_webhook_service()
    service.verify_signature(raw, signature)

    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AppError(ErrorCode.INVALID_PARAMS, "webhook body must be json") from exc
    if not isinstance(payload, dict):
        raise AppError(ErrorCode.INVALID_PARAMS, "webhook body must be a json object")

    result = await service.apply_payload(payload)
    return Response(
        data=WebhookApplyResponse(
            status=result.status,
            event_id=result.event_id,
            credits_granted=result.credits_granted,
            balance_after=result.balance_after,
        )
    )
