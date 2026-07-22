from urllib.parse import urlencode

from app.server.infra.config import settings
from app.integrations.aliyun_dm import send_template_mail


async def send_register_code_email(email: str, code: str) -> None:
    query = urlencode({"email": email})
    register_url = f"{settings.FRONTEND_BASE_URL}/register?{query}"
    await send_template_mail(
        to_address=email,
        subject="Nexus Studio 注册验证码",
        template_id=settings.ALIYUN_DM_TEMPLATE_ID_REGISTER,
        template_data={
            "Code": code,
            "RegisterUrl": register_url,
        },
    )


async def send_password_reset_email(to_email: str, reset_url: str) -> None:
    await send_template_mail(
        to_address=to_email,
        subject="Nexus Studio 重置密码",
        template_id=settings.ALIYUN_DM_TEMPLATE_ID_RESET,
        template_data={
            "ResetUrl": reset_url,
            "ExpireMinutes": str(settings.PASSWORD_RESET_TOKEN_EXPIRE_MINUTES),
        },
    )
