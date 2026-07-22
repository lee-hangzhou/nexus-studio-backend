from __future__ import annotations

from typing import Any

_generation_service: Any | None = None


def bind_generation_service(service: Any) -> None:
    global _generation_service
    _generation_service = service


def get_generation_service() -> Any:
    if _generation_service is None:
        raise RuntimeError("GenerationService is not bound")
    return _generation_service
