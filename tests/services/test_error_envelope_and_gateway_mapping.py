import json

from app.server.exceptions.base import AppError
from app.server.exceptions.codes import ErrorCode, HTTP_STATUS_BY_ERROR_CODE, http_status_for_error_code
from app.server.exceptions.envelope import error_envelope
from app.server.exceptions.response import json_error_response
from app.server.infra.gateway_mapping import (
    app_error_from_gateway_http_status,
    default_message_for_product_code,
    map_gateway_business_code,
    resolve_gateway_failure_message,
    resolve_product_error_code,
)


def test_error_envelope_is_pure_data() -> None:
    envelope = error_envelope(code=int(ErrorCode.INVALID_PARAMS), msg="bad", data={"k": 1})
    assert envelope.status_code == 400
    assert envelope.body == {"code": 40009, "data": {"k": 1}, "msg": "bad"}


def test_json_error_response_uses_status_table() -> None:
    response = json_error_response(code=int(ErrorCode.INTERNAL_ERROR), msg="boom")
    assert response.status_code == 500
    assert json.loads(response.body)["code"] == 50001


def test_http_status_table_covers_all_error_codes() -> None:
    assert set(HTTP_STATUS_BY_ERROR_CODE) == set(ErrorCode)


def test_invalid_token_uses_default_message_and_401() -> None:
    exc = AppError(ErrorCode.INVALID_TOKEN)
    assert exc.message == "Invalid or expired token"
    assert exc.status_code == 401
    assert http_status_for_error_code(int(ErrorCode.INVALID_TOKEN)) == 401
    response = json_error_response(code=int(ErrorCode.INVALID_TOKEN), msg=exc.message)
    assert response.status_code == 401
    assert json.loads(response.body)["code"] == 40002


def test_rate_limit_envelope_keeps_retry_after_header() -> None:
    response = json_error_response(
        code=int(ErrorCode.RATE_LIMITED),
        msg="Rate limit exceeded",
        data={"retry_after": 3},
        headers={"Retry-After": "3"},
    )
    assert response.status_code == 429
    assert response.headers.get("retry-after") == "3"


def test_map_gateway_client_codes() -> None:
    err_obj = map_gateway_business_code(10001, "params")
    assert err_obj.code == int(ErrorCode.INVALID_PARAMS)
    assert err_obj.details == {"gateway_code": 10001}

    err_obj = map_gateway_business_code(10003, None)
    assert err_obj.code == int(ErrorCode.GATEWAY_QUOTA_OR_RATE_LIMITED)

    err_obj = map_gateway_business_code(10105, "gone")
    assert err_obj.code == int(ErrorCode.TASK_NOT_FOUND)


def test_map_gateway_upstream_and_protocol() -> None:
    err_obj = map_gateway_business_code(20002, "blocked", request_id="r1")
    assert err_obj.code == int(ErrorCode.GATEWAY_UPSTREAM_ERROR)
    assert err_obj.details["gateway_code"] == 20002
    assert err_obj.details["request_id"] == "r1"

    err_obj = map_gateway_business_code(20003, "rate")
    assert err_obj.code == int(ErrorCode.GATEWAY_QUOTA_OR_RATE_LIMITED)
    assert err_obj.status_code == 429

    err_obj = map_gateway_business_code(20005, "quota")
    assert err_obj.code == int(ErrorCode.GATEWAY_QUOTA_OR_RATE_LIMITED)

    err_obj = map_gateway_business_code(20004, "timeout")
    assert err_obj.code == int(ErrorCode.SERVICE_UNAVAILABLE)
    assert err_obj.status_code == 503

    err_obj = map_gateway_business_code(50001, "x")
    assert err_obj.code == int(ErrorCode.GATEWAY_PROTOCOL_ERROR)

    err_obj = map_gateway_business_code(99999, "unknown")
    assert err_obj.code == int(ErrorCode.GATEWAY_PROTOCOL_ERROR)


def test_app_error_from_gateway_http_status() -> None:
    err_obj = app_error_from_gateway_http_status(503, request_id="r1")
    assert err_obj.code == int(ErrorCode.SERVICE_UNAVAILABLE)
    err_obj = app_error_from_gateway_http_status(429)
    assert err_obj.code == int(ErrorCode.GATEWAY_QUOTA_OR_RATE_LIMITED)
    err_obj = app_error_from_gateway_http_status(500)
    assert err_obj.code == int(ErrorCode.GATEWAY_UPSTREAM_ERROR)


def test_resolve_gateway_failure_message_does_not_build_app_error() -> None:
    assert resolve_gateway_failure_message(20002, "blocked") == "blocked"
    assert resolve_gateway_failure_message(None, "only-reason") == "only-reason"
    assert resolve_gateway_failure_message(10007, None) == default_message_for_product_code(
        resolve_product_error_code(10007)
    )
    assert resolve_gateway_failure_message(None, None) == ""
    assert resolve_product_error_code(20003) == ErrorCode.GATEWAY_QUOTA_OR_RATE_LIMITED


def test_app_error_rate_limited_status() -> None:
    err_obj = AppError(ErrorCode.RATE_LIMITED, "Rate limit exceeded", {"retry_after": 2})
    assert err_obj.status_code == 429


def test_stream_error_class_for_app_error_aligns_with_sse() -> None:
    from app.server.chat.domain.stream_enums import StreamErrorCode, normalize_stream_error_code
    from app.agent.runtime.agent.gateway_fail import stream_error_class_for_app_error

    timeout = stream_error_class_for_app_error(AppError(ErrorCode.SERVICE_UNAVAILABLE, "x"))
    assert timeout == "gateway_upstream_timeout"
    assert normalize_stream_error_code(timeout) == StreamErrorCode.GATEWAY_UPSTREAM_FAILED

    upstream = stream_error_class_for_app_error(AppError(ErrorCode.GATEWAY_UPSTREAM_ERROR, "x"))
    assert upstream == "gateway_upstream_failed"
    assert normalize_stream_error_code(upstream) == StreamErrorCode.GATEWAY_UPSTREAM_FAILED

    assert normalize_stream_error_code("app_error_50301") == StreamErrorCode.INTERNAL
