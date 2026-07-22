from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from app.server.exceptions.codes import http_status_for_error_code


@dataclass(frozen=True, slots=True)
class ErrorEnvelope:
    """产品错误信封：纯数据，不绑定 Web 框架。"""

    code: int
    msg: str
    data: Any
    status_code: int
    headers: Mapping[str, str] | None = None

    @property
    def body(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "data": self.data,
            "msg": self.msg,
        }


def error_envelope(
    *,
    code: int,
    msg: str,
    data: Any = None,
    status_code: int | None = None,
    headers: Mapping[str, str] | None = None,
) -> ErrorEnvelope:
    return ErrorEnvelope(
        code=code,
        msg=msg,
        data=data,
        status_code=status_code if status_code is not None else http_status_for_error_code(code),
        headers=headers,
    )
