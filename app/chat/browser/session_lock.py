"""Per-conversation page operation lock (single uvicorn worker; Redis if multi-worker)."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from contextlib import asynccontextmanager
from typing import AsyncIterator

_locks: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)


@asynccontextmanager
async def session_page_lock(conversation_id: int) -> AsyncIterator[None]:
    lock = _locks[conversation_id]
    async with lock:
        yield
