from __future__ import annotations

from typing import Any

from app.core.gateway import gateway_client
from app.core.logger import logger
from app.exceptions.base import AppError
from app.exceptions.codes import ErrorCode

MODEL_CAPABILITY_UNAVAILABLE = "模型能力信息暂时不可用，请稍后重试"


class ModelCatalog:
    """union_lm list_models 的 supports_vision 内存缓存；启动拉取失败时为空。"""

    def __init__(self) -> None:
        self._supports_vision: dict[str, bool] = {}

    def clear(self) -> None:
        self._supports_vision.clear()

    def update_from_gateway_rows(self, rows: list[dict[str, Any]]) -> None:
        for row in rows:
            if not isinstance(row, dict):
                continue
            if row.get("task_type") != 1:
                continue
            model_id = row.get("id")
            if not isinstance(model_id, str) or not model_id:
                continue
            value = row.get("supports_vision")
            if not isinstance(value, bool):
                continue
            self._supports_vision[model_id] = value

    def require_supports_vision(self, model_id: str) -> bool:
        if model_id not in self._supports_vision:
            raise AppError(ErrorCode.INVALID_PARAMS, MODEL_CAPABILITY_UNAVAILABLE)
        return self._supports_vision[model_id]

    def has(self, model_id: str) -> bool:
        return model_id in self._supports_vision

    def known_model_ids(self) -> tuple[str, ...]:
        return tuple(self._supports_vision)


model_catalog = ModelCatalog()


def parse_chat_model_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = payload.get("data")
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


async def try_refresh_model_catalog() -> None:
    try:
        payload = await gateway_client.list_openai_models()
        rows = parse_chat_model_rows(payload)
        if rows:
            model_catalog.update_from_gateway_rows(rows)
            logger.info("model_catalog.refreshed", model_count=len(model_catalog._supports_vision))
    except Exception as exc:
        logger.warning("model_catalog.refresh_skipped", error=str(exc))
