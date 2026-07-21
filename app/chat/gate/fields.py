"""Normalize gate field definitions across agent tool args, SSE, and vault."""

from __future__ import annotations

from typing import Any


def field_def_name(item: dict[str, Any]) -> str:
    """Return the canonical field name required by the gate contract."""
    value = item.get("name")
    return value.strip() if isinstance(value, str) else ""


def field_names(field_defs: list[dict[str, Any]]) -> set[str]:
    return {name for item in field_defs if (name := field_def_name(item))}


def normalize_field_defs(field_defs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Validate and retain field definitions with their canonical name."""
    out: list[dict[str, Any]] = []
    for raw in field_defs:
        if not isinstance(raw, dict):
            continue
        name = field_def_name(raw)
        if not name:
            continue
        out.append(dict(raw))
    return out
