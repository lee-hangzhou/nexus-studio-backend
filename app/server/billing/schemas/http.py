from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.server.billing.domain.packs import CreditPackKey


class CreditPackView(BaseModel):
    key: CreditPackKey
    label: str
    price_usd_cents: int = Field(ge=1)
    credits: int = Field(ge=1)
    available: bool


class CreditPackListResponse(BaseModel):
    packs: list[CreditPackView]
    credits_per_usd: int = Field(ge=1)


class CreateCheckoutRequest(BaseModel):
    pack_key: CreditPackKey


class CreateCheckoutResponse(BaseModel):
    order_id: int
    request_id: str
    pack_key: CreditPackKey
    credits: int
    price_usd_cents: int
    checkout_url: str


class CreditBalanceResponse(BaseModel):
    balance: int = Field(ge=0)


class WebhookApplyResponse(BaseModel):
    status: Literal["applied", "duplicate", "ignored"]
    event_id: str | None = None
    credits_granted: int = 0
    balance_after: int | None = None
