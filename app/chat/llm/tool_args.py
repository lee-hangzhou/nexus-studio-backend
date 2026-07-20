import json
import re
from typing import Any


def repair_tool_arguments(raw: str) -> tuple[dict[str, Any] | None, str | None]:
    text = (raw or "").strip()
    if not text:
        return {}, None
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed, None
        return None, "arguments must be a JSON object"
    except json.JSONDecodeError as exc:
        # Gateway/model sometimes glue two JSON objects in one arguments string.
        if "Extra data" in str(exc):
            try:
                first, _end = json.JSONDecoder().raw_decode(text)
                if isinstance(first, dict):
                    return first, None
            except json.JSONDecodeError:
                pass
        return None, str(exc)
