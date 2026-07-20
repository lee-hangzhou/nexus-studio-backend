"""Bocha Web Search API client for the web_search tool."""

from __future__ import annotations

import re
from typing import Any

import httpx

from app.core.config import settings

# Bocha Web Search API: freshness request values (see bocha-ai.feishu.cn wiki).
BOCHA_FRESHNESS_PRESETS = frozenset(
    {"noLimit", "oneDay", "oneWeek", "oneMonth", "oneYear"},
)

# Response item fields for publish time (Bing-compatible webPages.value[]).
BOCHA_PUBLISHED_FIELD = "datePublished"
BOCHA_CRAWLED_FIELD = "dateLastCrawled"

_BOCHA_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_BOCHA_DATE_RANGE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\.\.\d{4}-\d{2}-\d{2}$")

BOCHA_DEFAULT_RESULT_COUNT = 5
WEB_SEARCH_SUMMARY_MAX_CHARS = 600


class InvalidFreshnessError(ValueError):
    """Raised when freshness fails mechanical format validation."""


def normalize_freshness(value: str | None) -> str | None:
    """Validate freshness; return None to omit the field from the API request."""
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    if cleaned in BOCHA_FRESHNESS_PRESETS:
        return cleaned
    if _BOCHA_DATE_RE.match(cleaned) or _BOCHA_DATE_RANGE_RE.match(cleaned):
        return cleaned
    allowed = ", ".join(sorted(BOCHA_FRESHNESS_PRESETS))
    raise InvalidFreshnessError(
        f"invalid freshness: {value!r}; allowed presets: {allowed}, "
        "or YYYY-MM-DD, or YYYY-MM-DD..YYYY-MM-DD"
    )


def _parse_bocha_web_pages(data: dict[str, Any]) -> list[dict[str, Any]]:
    web_pages = (data.get("data") or {}).get("webPages") or {}
    value = web_pages.get("value")
    if isinstance(value, list):
        return value
    return []


def _published_at(item: dict[str, Any]) -> str:
    for key in (BOCHA_PUBLISHED_FIELD, BOCHA_CRAWLED_FIELD):
        raw = item.get(key)
        if raw:
            return str(raw)
    return "未知"


def _truncate_summary(text: str, *, max_chars: int = WEB_SEARCH_SUMMARY_MAX_CHARS) -> str:
    cleaned = text.strip()
    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[: max_chars - 1] + "…"


def _format_results(items: list[dict[str, Any]], *, limit: int) -> str:
    if not items:
        return "No results found."

    lines: list[str] = []
    for item in items[:limit]:
        title = item.get("name") or item.get("title") or ""
        url = item.get("url") or ""
        body = _truncate_summary(
            item.get("summary") or item.get("snippet") or item.get("description") or ""
        )
        site = item.get("siteName") or ""
        published = _published_at(item)
        prefix = f"- {title}: {url}"
        if site:
            prefix = f"- {title} ({site}): {url}"
        lines.append(f"{prefix}\n  发布时间：{published}\n  摘要：{body}".rstrip())
    return "\n".join(lines)


def build_bocha_search_payload(*, query: str, freshness: str | None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "query": query,
        "summary": True,
        "count": BOCHA_DEFAULT_RESULT_COUNT,
    }
    if freshness is not None:
        payload["freshness"] = freshness
    return payload


async def search_web(query: str, *, freshness: str | None = None) -> str:
    api_key = (settings.BOCHA_API_KEY or "").strip()
    if not api_key:
        return "web_search unavailable: BOCHA_API_KEY not configured"

    payload = build_bocha_search_payload(query=query, freshness=freshness)
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=settings.WEB_TOOL_TIMEOUT_SECONDS) as client:
        response = await client.post(
            settings.BOCHA_WEB_SEARCH_URL,
            headers=headers,
            json=payload,
        )
        response.raise_for_status()
        data = response.json()

    code = data.get("code")
    if code is not None and code != 200:
        message = data.get("msg") or data.get("message") or "unknown error"
        return f"web_search unavailable: Bocha API error ({code}): {message}"

    items = _parse_bocha_web_pages(data)
    return _format_results(items, limit=BOCHA_DEFAULT_RESULT_COUNT)
