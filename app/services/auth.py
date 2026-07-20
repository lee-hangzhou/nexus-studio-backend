from urllib.parse import urlencode

from app.core.config import settings
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    generate_password_reset_token,
    get_password_hash,
    verify_password,
)
from app.exceptions.base import (
    InvalidCredentials,
    InvalidPasswordResetToken,
    InvalidToken,
    UserAlreadyExists,
    UserInactive,
    UserNotFound,
)
from app.repositories.user import UserRepository
from app.schemas.auth import (
    ForgotPasswordRequest,
    LoginResponse,
    MessageResponse,
    RegisterRequest,
    ResetPasswordRequest,
    SendRegisterCodeRequest,
    TokenResponse,
)
from app.schemas.user import UserInfo
from app.services.auth_tokens import (
    is_refresh_token_active,
    pop_password_reset_user_id,
    revoke_all_refresh_tokens,
    revoke_refresh_token,
    store_password_reset_token,
    store_refresh_token,
)
from app.services.register_codes import issue_register_code, verify_and_consume_register_code
from app.utils.email import send_password_reset_email, send_register_code_email


class AuthService:
    def __init__(self) -> None:
        self.user_repo = UserRepository()

    @staticmethod
    def _access_expires_in() -> int:
        return settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60

    async def _issue_tokens(self, user_id: int) -> LoginResponse:
        access_token = create_access_token(subject=user_id)
        refresh_token = create_refresh_token(subject=user_id)

        refresh_payload = decode_token(refresh_token)
        if not refresh_payload:
            raise InvalidToken

        jti = refresh_payload.get("jti")
        if not isinstance(jti, str) or not jti:
            raise InvalidToken

        await store_refresh_token(user_id, jti)

        return LoginResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=self._access_expires_in(),
        )

    async def send_register_code(self, data: SendRegisterCodeRequest) -> MessageResponse:
        message = MessageResponse(message="若该邮箱尚未注册，将收到验证码邮件")
        existing = await self.user_repo.get_by_email(data.email)
        if existing:
            return message

        code = await issue_register_code(str(data.email))
        await send_register_code_email(str(data.email), code)
        return message

    async def register(self, data: RegisterRequest) -> LoginResponse:
        await verify_and_consume_register_code(str(data.email), data.code)

        existing = await self.user_repo.get_by_username(data.username)
        if existing:
            raise UserAlreadyExists

        existing = await self.user_repo.get_by_email(data.email)
        if existing:
            raise UserAlreadyExists

        user = await self.user_repo.create_user(
            username=data.username,
            email=data.email,
            hashed_password=get_password_hash(data.password),
        )
        return await self._issue_tokens(user.id)

    async def login(self, email: str, password: str) -> LoginResponse:
        user = await self.user_repo.get_by_email(email)
        if not user or not verify_password(password, user.hashed_password):
            raise InvalidCredentials

        if not user.is_active:
            raise UserInactive

        return await self._issue_tokens(user.id)

    async def refresh_token(self, refresh_token: str) -> TokenResponse:
        payload = decode_token(refresh_token)
        if not payload or payload.get("type") != "refresh":
            raise InvalidToken

        user_id_raw = payload.get("sub")
        jti = payload.get("jti")
        if not user_id_raw or not isinstance(jti, str) or not jti:
            raise InvalidToken

        try:
            user_id = int(user_id_raw)
        except (TypeError, ValueError) as exc:
            raise InvalidToken from exc

        user = await self.user_repo.get_by_id(user_id)
        if not user:
            raise UserNotFound
        if not user.is_active:
            raise UserInactive

        if not await is_refresh_token_active(jti, user_id):
            raise InvalidToken

        await revoke_refresh_token(user_id, jti)
        return await self._issue_tokens(user_id)

    async def logout(self, user_id: int, refresh_token: str | None) -> None:
        if refresh_token:
            payload = decode_token(refresh_token)
            if payload and payload.get("type") == "refresh":
                jti = payload.get("jti")
                if isinstance(jti, str) and jti:
                    await revoke_refresh_token(user_id, jti)
                    return

        await revoke_all_refresh_tokens(user_id)

    async def forgot_password(self, data: ForgotPasswordRequest) -> MessageResponse:
        message = MessageResponse(message="若该邮箱已注册，将收到重置密码邮件")
        user = await self.user_repo.get_by_email(data.email)
        if not user or not user.is_active:
            return message

        token = generate_password_reset_token()
        await store_password_reset_token(token, user.id)

        query = urlencode({"token": token})
        reset_url = f"{settings.FRONTEND_RESET_PASSWORD_URL}?{query}"
        await send_password_reset_email(user.email, reset_url)

        return message

    async def reset_password(self, data: ResetPasswordRequest) -> MessageResponse:
        user_id = await pop_password_reset_user_id(data.token)
        if user_id is None:
            raise InvalidPasswordResetToken

        user = await self.user_repo.get_by_id(user_id)
        if not user:
            raise UserNotFound
        if not user.is_active:
            raise UserInactive

        await self.user_repo.update_password(user_id, get_password_hash(data.new_password))
        await revoke_all_refresh_tokens(user_id)

        return MessageResponse(message="密码已重置，请使用新密码登录")

    async def get_current_user(self, user_id: int) -> UserInfo:
        user = await self.user_repo.get_by_id(user_id)
        if not user:
            raise UserNotFound
        return UserInfo.model_validate(user)
