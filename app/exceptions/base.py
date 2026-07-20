from typing import Any, Dict, Optional

from app.exceptions.codes import ErrorCode


class AppError(Exception):
    def __init__(
        self,
        code: int | ErrorCode,
        message: str = "",
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.code = int(code)
        self.message = message
        self.details = details
        super().__init__(self.message)

    @property
    def status_code(self) -> int:
        if self.code < 1000:
            return self.code
        return self.code // 100


# 400xx - Client errors
InvalidCredentials = AppError(ErrorCode.INVALID_CREDENTIALS, "Invalid username or password")
InvalidToken = AppError(ErrorCode.INVALID_TOKEN, "Invalid or expired token")
PermissionDenied = AppError(ErrorCode.PERMISSION_DENIED, "Permission denied")
UserAlreadyExists = AppError(ErrorCode.USER_ALREADY_EXISTS, "User already exists")
UserInactive = AppError(ErrorCode.USER_INACTIVE, "User account is inactive")
InvalidPasswordResetToken = AppError(
    ErrorCode.INVALID_PASSWORD_RESET_TOKEN,
    "Invalid or expired password reset token",
)
RegisterCodeRateLimited = AppError(
    ErrorCode.REGISTER_CODE_RATE_LIMITED,
    "Please wait before requesting another verification code",
)
InvalidVerificationCode = AppError(
    ErrorCode.INVALID_VERIFICATION_CODE,
    "Invalid or expired verification code",
)

# 404xx - Not found
NotFound = AppError(ErrorCode.RESOURCE_NOT_FOUND, "Resource not found")
UserNotFound = AppError(ErrorCode.USER_NOT_FOUND, "User not found")

# 500xx - Server errors
InternalError = AppError(ErrorCode.INTERNAL_ERROR, "Internal server error")
