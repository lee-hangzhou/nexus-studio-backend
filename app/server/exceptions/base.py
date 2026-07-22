from typing import Any, Dict, Optional

from app.server.exceptions.codes import (
    DEFAULT_ERROR_MESSAGES,
    ErrorCode,
    http_status_for_error_code,
)


class AppError(Exception):
    def __init__(
        self,
        code: int | ErrorCode,
        message: str = "",
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.code = int(code)
        if message:
            self.message = message
        else:
            try:
                self.message = DEFAULT_ERROR_MESSAGES.get(ErrorCode(self.code), "")
            except ValueError:
                self.message = ""
        self.details = details
        super().__init__(self.message)

    @property
    def status_code(self) -> int:
        return http_status_for_error_code(self.code)
