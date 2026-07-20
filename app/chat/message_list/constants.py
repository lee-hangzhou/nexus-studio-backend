"""Chat message list API — shared limits and cache namespace."""

DEFAULT_TURN_LIMIT = 50
MAX_TURN_LIMIT = 100

LIST_CACHE_KEY_PREFIX = "chat:msglist:v1"

# Metadata keys omitted from list responses (audit duplicates tool_steps for UI).
LIST_METADATA_OMIT_KEYS = frozenset({"tool_audit"})

# Tool row content cap when result_preview is absent (timeline fallback only).
TOOL_LIST_CONTENT_MAX_CHARS = 500
