import httpx

from app.core.config import settings


async def fetch_url(url: str) -> str:
    async with httpx.AsyncClient(
        timeout=settings.WEB_TOOL_TIMEOUT_SECONDS,
        follow_redirects=True,
    ) as client:
        response = await client.get(url)
        response.raise_for_status()
        text = response.text
    return text[:12_000]
