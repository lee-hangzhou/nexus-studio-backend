"""请求校验错误应对用户返回中文可恢复提示"""

from __future__ import annotations

from fastapi.exceptions import RequestValidationError

from app.server.exceptions.handlers import validation_error_handler


class _Req:
    url = type("U", (), {"path": "/api/v1/workshop/connectors/list"})()
    method = "POST"


async def test_validation_error_message_is_chinese() -> None:
    """校验失败不返回英文 Validation error"""
    exc = RequestValidationError(
        [{"type": "missing", "loc": ("body", "project_id"), "msg": "Field required", "input": {}}]
    )
    response = await validation_error_handler(_Req(), exc)
    body = response.body.decode("utf-8")
    assert "Validation error" not in body
    assert "请求参数无效" in body
