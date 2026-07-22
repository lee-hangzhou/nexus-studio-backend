from __future__ import annotations

from dataclasses import dataclass
from typing import Any, NotRequired, TypedDict

from app.contracts.gateway import GatewayResultItem
from app.server.exceptions.base import AppError
from app.server.exceptions.codes import ErrorCode
from app.server.generation.domain.gateway_status import GatewayTaskStatus


class GenerationTerminalUpdate(TypedDict):
    status: int
    callback_sent: bool
    result_keys: NotRequired[list[dict[str, Any]]]
    error_message: NotRequired[str]


@dataclass(frozen=True)
class GenerationTerminal:
    status: GatewayTaskStatus
    result_keys: list[dict[str, Any]] | None
    error_message: str | None

    def update_fields(self, *, callback_sent: bool) -> GenerationTerminalUpdate:
        fields: GenerationTerminalUpdate = {
            "status": int(self.status),
            "callback_sent": callback_sent,
        }
        if self.result_keys is not None:
            fields["result_keys"] = self.result_keys
        if self.error_message is not None:
            fields["error_message"] = self.error_message
        return fields


def normalize_gateway_terminal(
    status: int | GatewayTaskStatus,
    urls: list[GatewayResultItem] | None,
    reason: str | None,
) -> GenerationTerminal:
    normalized_status = GatewayTaskStatus(status)
    result_keys = None
    error_message = reason
    if normalized_status == GatewayTaskStatus.SUCCEEDED:
        if not urls:
            normalized_status = GatewayTaskStatus.FAILED
            error_message = "成功终态缺少生成结果"
        else:
            result_keys = [item.model_dump(mode="json", exclude_none=False) for item in urls]
    elif normalized_status == GatewayTaskStatus.FAILED:
        if not reason:
            raise AppError(ErrorCode.GATEWAY_PROTOCOL_ERROR, "失败终态缺少错误原因")
    elif urls:
        raise AppError(ErrorCode.GATEWAY_PROTOCOL_ERROR, "非成功终态不能携带生成结果")
    return GenerationTerminal(
        status=normalized_status,
        result_keys=result_keys,
        error_message=error_message,
    )
