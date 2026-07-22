from __future__ import annotations

import json

from app.agent.chat.llm.model_catalog import model_catalog
from app.server.infra.config import settings


def resolve_memory_extract_model_key() -> str | None:
    """返回 model_catalog 中可用的 chat 模型 key, 无则 None"""
    preferred = (settings.CANVAS_MEMORY_EXTRACT_MODEL or "").strip()
    if preferred and model_catalog.has(preferred):
        return preferred
    try:
        raw = json.loads(settings.CHAT_MODEL_REGISTRY)
    except json.JSONDecodeError:
        raw = {}
    if isinstance(raw, dict):
        for key in raw:
            if isinstance(key, str) and model_catalog.has(key):
                return key
    known = model_catalog.known_model_ids()
    return known[0] if known else None
