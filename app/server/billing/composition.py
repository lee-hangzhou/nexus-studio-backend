from __future__ import annotations

from functools import lru_cache

from app.server.billing.adapters.creem_client import CreemClient
from app.server.billing.domain.packs import CREDIT_PACKS, CreditPackKey
from app.server.billing.persistence.models import TortoiseBillingRepository
from app.server.billing.schemas.http import CreditPackView
from app.server.billing.services.checkout import BillingCheckoutService
from app.server.billing.services.webhook import BillingWebhookService
from app.server.infra.config import settings


def product_id_by_pack_from_settings() -> dict[CreditPackKey, str]:
    """从配置读取已启用的 Creem product id 映射"""
    mapping = {
        CreditPackKey.USD_5: settings.CREEM_PRODUCT_ID_USD_5,
        CreditPackKey.USD_15: settings.CREEM_PRODUCT_ID_USD_15,
        CreditPackKey.USD_50: settings.CREEM_PRODUCT_ID_USD_50,
        CreditPackKey.USD_100: settings.CREEM_PRODUCT_ID_USD_100,
    }
    return {key: value.strip() for key, value in mapping.items() if value.strip()}


def billing_success_url() -> str:
    """返回已配置的支付成功回跳 URL；未配置时返回空串（由用例 fail-closed）"""
    return str(settings.CREEM_SUCCESS_URL).strip()


@lru_cache(maxsize=1)
def get_billing_repository() -> TortoiseBillingRepository:
    """组合根：账单仓储单例"""
    return TortoiseBillingRepository()


@lru_cache(maxsize=1)
def get_creem_client() -> CreemClient:
    """组合根：Creem HTTP 客户端单例"""
    return CreemClient(
        api_key=settings.CREEM_API_KEY,
        base_url=settings.CREEM_API_BASE_URL,
    )


def get_checkout_service() -> BillingCheckoutService:
    """组合根：创建一次性积分包 checkout"""
    repo = get_billing_repository()
    return BillingCheckoutService(
        product_id_by_pack=product_id_by_pack_from_settings(),
        success_url=billing_success_url(),
        orders=repo,
        creem=get_creem_client(),
    )


def get_webhook_service() -> BillingWebhookService:
    """组合根：处理 Creem webhook 并入账"""
    repo = get_billing_repository()
    return BillingWebhookService(
        webhook_secret=settings.CREEM_WEBHOOK_SECRET,
        product_id_by_pack=product_id_by_pack_from_settings(),
        orders=repo,
        balances=repo,
        events=repo,
    )


async def get_user_credit_balance(user_id: int) -> int:
    """查询用户积分余额"""
    return await get_billing_repository().get_balance(user_id)


def list_public_packs() -> list[CreditPackView]:
    """列出公开积分包及是否已配置可购"""
    configured = product_id_by_pack_from_settings()
    return [
        CreditPackView(
            key=pack.key,
            label=pack.label,
            price_usd_cents=pack.price_usd_cents,
            credits=pack.credits,
            available=key in configured,
        )
        for key, pack in CREDIT_PACKS.items()
    ]
