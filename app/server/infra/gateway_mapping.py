from __future__ import annotations

from typing import Any

from app.server.exceptions.base import AppError
from app.server.exceptions.codes import ErrorCode

# union_lm errorx 调用方错误
_GATEWAY_CLIENT_CODES: dict[int, ErrorCode] = {
    10001: ErrorCode.INVALID_PARAMS,
    10002: ErrorCode.INVALID_TOKEN,
    10003: ErrorCode.GATEWAY_QUOTA_OR_RATE_LIMITED,
    10004: ErrorCode.PERMISSION_DENIED,
    10006: ErrorCode.GATEWAY_QUOTA_OR_RATE_LIMITED,
    10007: ErrorCode.GATEWAY_QUOTA_OR_RATE_LIMITED,
    10008: ErrorCode.TASK_CANCEL_FAILED,
    10009: ErrorCode.TASK_CANCEL_FAILED,
    10105: ErrorCode.TASK_NOT_FOUND,
}

# 上游可退避错误：与通用 GATEWAY_UPSTREAM_ERROR 区分
_GATEWAY_UPSTREAM_CODES: dict[int, ErrorCode] = {
    20003: ErrorCode.GATEWAY_QUOTA_OR_RATE_LIMITED,  # ErrUpstreamRateLimit
    20004: ErrorCode.SERVICE_UNAVAILABLE,  # ErrUpstreamTimeout
    20005: ErrorCode.GATEWAY_QUOTA_OR_RATE_LIMITED,  # ErrUpstreamQuota
}

_PRODUCT_MESSAGES: dict[ErrorCode, str] = {
    ErrorCode.INVALID_PARAMS: "请求参数无效",
    ErrorCode.INVALID_TOKEN: "网关鉴权失败",
    ErrorCode.PERMISSION_DENIED: "无权访问上游资源",
    ErrorCode.GATEWAY_QUOTA_OR_RATE_LIMITED: "上游配额不足或请求过于频繁",
    ErrorCode.TASK_CANCEL_FAILED: "任务无法取消",
    ErrorCode.TASK_NOT_FOUND: "上游任务不存在",
    ErrorCode.GATEWAY_UPSTREAM_ERROR: "上游模型服务失败",
    ErrorCode.GATEWAY_PROTOCOL_ERROR: "模型网关返回了不符合协议的数据",
    ErrorCode.GATEWAY_SUBMIT_ERROR: "提交生成任务失败：上游服务暂不可用",
    ErrorCode.GENERATION_STATUS_UNAVAILABLE: "任务状态暂时不可用",
    ErrorCode.GENERATION_QUEUE_UNAVAILABLE: "排队信息暂时不可用",
    ErrorCode.GENERATION_MODEL_LIST_UNAVAILABLE: "模型或音色列表暂时不可用",
    ErrorCode.SERVICE_UNAVAILABLE: "上游请求超时，请稍后重试",
}


def resolve_product_error_code(gateway_code: int) -> ErrorCode:
    """已知码一对一映射；其余 2xxxx 归为上游失败，5xxxx/未知归为协议错误。

    details.gateway_code 仍保留原始码，产品 ErrorCode 允许有意折叠未单独列出的上游失败。
    """
    if gateway_code in _GATEWAY_CLIENT_CODES:
        return _GATEWAY_CLIENT_CODES[gateway_code]
    if gateway_code in _GATEWAY_UPSTREAM_CODES:
        return _GATEWAY_UPSTREAM_CODES[gateway_code]
    if 20000 <= gateway_code < 30000:
        return ErrorCode.GATEWAY_UPSTREAM_ERROR
    if gateway_code >= 50000:
        return ErrorCode.GATEWAY_PROTOCOL_ERROR
    return ErrorCode.GATEWAY_PROTOCOL_ERROR


def default_message_for_product_code(product: ErrorCode) -> str:
    return _PRODUCT_MESSAGES.get(product, "上游请求失败")


def resolve_gateway_failure_message(
    gateway_code: int | None,
    gateway_message: str | None = None,
) -> str:
    message = (gateway_message or "").strip()
    if message:
        return message
    if gateway_code is None:
        return ""
    return default_message_for_product_code(resolve_product_error_code(gateway_code))


def map_gateway_business_code(
    gateway_code: int,
    gateway_message: str | None = None,
    *,
    request_id: str | None = None,
    response_model: str | None = None,
) -> AppError:
    """按 resolve_product_error_code 映射为 AppError；未单独列出的 2xxxx 会折叠为 GATEWAY_UPSTREAM_ERROR。"""
    product = resolve_product_error_code(gateway_code)
    message = (gateway_message or "").strip() or default_message_for_product_code(product)
    details: dict[str, Any] = {"gateway_code": gateway_code}
    if request_id:
        details["request_id"] = request_id
    if response_model:
        details["response_model"] = response_model
    return AppError(product, message, details)


def app_error_from_gateway_http_status(
    status_code: int,
    *,
    request_id: str | None = None,
    endpoint: str | None = None,
) -> AppError:
    """Chat/completions 等非信封 HTTP 失败 → 产品 AppError。"""
    details: dict[str, Any] = {"status_code": status_code}
    if request_id:
        details["request_id"] = request_id
    if endpoint:
        details["endpoint"] = endpoint
    if status_code == 429:
        return AppError(
            ErrorCode.GATEWAY_QUOTA_OR_RATE_LIMITED,
            default_message_for_product_code(ErrorCode.GATEWAY_QUOTA_OR_RATE_LIMITED),
            details,
        )
    if status_code in {502, 503, 504, 524}:
        return AppError(ErrorCode.SERVICE_UNAVAILABLE, "模型网关暂时不可用", details)
    if status_code == 401:
        return AppError(ErrorCode.INVALID_TOKEN, "网关鉴权失败", details)
    if status_code == 403:
        return AppError(ErrorCode.PERMISSION_DENIED, "无权访问上游资源", details)
    if status_code == 400:
        return AppError(ErrorCode.INVALID_PARAMS, "请求参数无效", details)
    return AppError(
        ErrorCode.GATEWAY_UPSTREAM_ERROR,
        f"模型网关 HTTP {status_code}",
        details,
    )
