import asyncio
from typing import Any

from app.server.infra.config import settings
from app.server.infra.logger import logger


def is_dm_configured() -> bool:
    return bool(settings.ALIYUN_ACCESS_KEY_ID.strip() and settings.ALIYUN_ACCESS_KEY_SECRET.strip())


def _send_template_mail_sync(
    to_address: str,
    subject: str,
    template_id: str,
    template_data: dict[str, Any],
) -> None:
    from alibabacloud_dm20151123 import models as dm_models
    from alibabacloud_dm20151123.client import Client as DmClient
    from alibabacloud_tea_openapi import models as open_api_models

    config = open_api_models.Config(
        access_key_id=settings.ALIYUN_ACCESS_KEY_ID,
        access_key_secret=settings.ALIYUN_ACCESS_KEY_SECRET,
        endpoint=settings.ALIYUN_DM_ENDPOINT,
    )
    client = DmClient(config)
    request = dm_models.SingleSendMailRequest(
        account_name=settings.ALIYUN_DM_ACCOUNT_NAME,
        address_type=1,
        reply_to_address=True,
        to_address=to_address,
        subject=subject,
        from_alias=settings.ALIYUN_DM_FROM_ALIAS,
        template=dm_models.SingleSendMailRequestTemplate(
            template_id=template_id,
            template_data={k: str(v) for k, v in template_data.items()},
        ),
    )
    client.single_send_mail(request)


async def send_template_mail(
    to_address: str,
    subject: str,
    template_id: str,
    template_data: dict[str, Any],
) -> None:
    if not is_dm_configured():
        logger.info(
            "aliyun_dm_skipped",
            to_address=to_address,
            subject=subject,
            template_id=template_id,
            template_data=template_data,
        )
        return

    await asyncio.to_thread(
        _send_template_mail_sync,
        to_address,
        subject,
        template_id,
        template_data,
    )
    logger.info("aliyun_dm_sent", to_address=to_address, subject=subject, template_id=template_id)
