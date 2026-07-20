"""Normalize gate field definitions across agent tool args, SSE, and vault."""

from __future__ import annotations

from typing import Any


def field_def_name(item: dict[str, Any]) -> str:
    """Return canonical field name; accept legacy agent `key` alias."""
    return str(item.get("name") or item.get("key") or "").strip()


def field_names(field_defs: list[dict[str, Any]]) -> set[str]:
    return {name for item in field_defs if (name := field_def_name(item))}


def normalize_field_defs(field_defs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ensure each field def exposes `name` for frontend forms and vault keys."""
    out: list[dict[str, Any]] = []
    for raw in field_defs:
        if not isinstance(raw, dict):
            continue
        name = field_def_name(raw)
        if not name:
            continue
        item = dict(raw)
        item["name"] = name
        out.append(item)
    return out
