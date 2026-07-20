from fastapi import APIRouter, Request

from app.schemas.auth import (
    ForgotPasswordRequest,
    LoginRequest,
    LoginResponse,
    LogoutRequest,
    MessageResponse,
    RefreshRequest,
    RegisterRequest,
    ResetPasswordRequest,
    SendRegisterCodeRequest,
    TokenResponse,
)
from app.schemas.base import Response
from app.schemas.user import UserInfo
from app.services import registry

router = APIRouter()


@router.post("/send-register-code")
async def send_register_code(request: SendRegisterCodeRequest) -> Response[MessageResponse]:
    result = await registry.auth_service.send_register_code(request)
    return Response(data=result)


@router.post("/register")
async def register(request: RegisterRequest) -> Response[LoginResponse]:
    result = await registry.auth_service.register(request)
    return Response(data=result)


@router.post("/login")
async def login(request: LoginRequest) -> Response[LoginResponse]:
    result = await registry.auth_service.login(str(request.email), request.password)
    return Response(data=result)


@router.post("/refresh")
async def refresh_token(request: RefreshRequest) -> Response[TokenResponse]:
    result = await registry.auth_service.refresh_token(request.refresh_token)
    return Response(data=result)


@router.post("/logout")
async def logout(request: Request, body: LogoutRequest) -> Response[MessageResponse]:
    user_id: int = request.state.user_id
    await registry.auth_service.logout(user_id, body.refresh_token)
    return Response(data=MessageResponse(message="已退出登录"))


@router.post("/forgot-password")
async def forgot_password(request: ForgotPasswordRequest) -> Response[MessageResponse]:
    result = await registry.auth_service.forgot_password(request)
    return Response(data=result)


@router.post("/reset-password")
async def reset_password(request: ResetPasswordRequest) -> Response[MessageResponse]:
    result = await registry.auth_service.reset_password(request)
    return Response(data=result)


@router.post("/me")
async def get_current_user(request: Request) -> Response[UserInfo]:
    user_id: int = request.state.user_id
    result = await registry.auth_service.get_current_user(user_id)
    return Response(data=result)
