from __future__ import annotations

from typing import Any

from pydantic import BaseModel


def unwrap_store_content(value: Any) -> Any:
    """从 content 或 kind/content 信封取出业务内容"""
    if not isinstance(value, dict):
        return value
    if "content" in value:
        return value["content"]
    return value


def canonical_project_value(payload: BaseModel) -> dict[str, Any]:
    """构造 create_memory_store_manager 期望的冷路径信封"""
    return {
        "kind": type(payload).__name__,
        "content": payload.model_dump(mode="json"),
    }


def memory_rows_for_model(items: list[Any], *, schema: type[BaseModel]) -> list[dict[str, Any]]:
    """将 search 结果序列化为模型可读的 [{id, ...fields}]"""
    rows: list[dict[str, Any]] = []
    for item in items:
        raw = unwrap_store_content(getattr(item, "value", None) or {})
        try:
            if isinstance(raw, schema):
                data = raw.model_dump(mode="json")
            elif isinstance(raw, dict):
                data = schema.model_validate(raw).model_dump(mode="json")
            else:
                continue
        except Exception:
            continue
        rows.append({"id": getattr(item, "key", None), **data})
    return rows
