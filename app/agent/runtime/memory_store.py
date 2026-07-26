from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import psycopg
from langgraph.store.base import BaseStore
from langgraph.store.postgres import AsyncPostgresStore

from app.server.infra.config import settings
from app.server.infra.embeddings import gateway_embeddings_client
from app.server.infra.logger import logger
from app.agent.runtime.runtime import runtime_resources


def get_memory_store() -> BaseStore | None:
    """Chat + Canvas 共用的 AsyncPostgresStore 单例。"""
    return runtime_resources.canvas_memory_store


def get_canvas_memory_store() -> BaseStore | None:
    return get_memory_store()


def set_canvas_memory_store(store: BaseStore | None) -> None:
    runtime_resources.canvas_memory_store = store


async def _embed_texts(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    return await gateway_embeddings_client.embed_texts_batch(
        texts,
        model=settings.CANVAS_MEMORY_EMBEDDING_MODEL,
        dimensions=settings.ASSET_VECTOR_DIMENSION,
        timeout_sec=float(settings.MEMORY_TOOL_TIMEOUT_SEC),
    )


async def _ensure_store_base_schema(uri: str) -> None:
    """Bootstrap LangGraph's non-vector tables before adding vector storage."""
    async with AsyncPostgresStore.from_conn_string(uri) as store:
        await store.setup()


async def _ensure_store_vector_schema(uri: str, *, dims: int) -> None:
    """LangGraph async setup 在 dims>2000 时会尝试建 HNSW 并失败；此处预建表并标记 migration。"""
    safe_dims = int(dims)
    async with await psycopg.AsyncConnection.connect(uri) as conn:
        async with conn.cursor() as cur:
            await cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
            await cur.execute(
                "CREATE TABLE IF NOT EXISTS vector_migrations (v INTEGER PRIMARY KEY)"
            )
            await cur.execute(
                """
                SELECT EXISTS (
                    SELECT 1 FROM information_schema.tables
                    WHERE table_schema = 'public' AND table_name = 'store_vectors'
                )
                """
            )
            table_exists = bool((await cur.fetchone())[0])
            await cur.execute("SELECT COALESCE(MAX(v), -1) FROM vector_migrations")
            max_v = int((await cur.fetchone())[0])

            if not table_exists:
                if max_v >= 0:
                    await cur.execute("DELETE FROM vector_migrations")
                await cur.execute(
                    f"""
                    CREATE TABLE store_vectors (
                        prefix text NOT NULL,
                        key text NOT NULL,
                        field_name text NOT NULL,
                        embedding vector({safe_dims}),
                        created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY (prefix, key, field_name)
                    )
                    """
                )
                for v in (0, 1, 2):
                    await cur.execute(
                        "INSERT INTO vector_migrations (v) VALUES (%s) ON CONFLICT DO NOTHING",
                        (v,),
                    )
            elif max_v < 2:
                for v in (0, 1, 2):
                    await cur.execute(
                        "INSERT INTO vector_migrations (v) VALUES (%s) ON CONFLICT DO NOTHING",
                        (v,),
                    )
            await conn.commit()


@asynccontextmanager
async def create_memory_store() -> AsyncGenerator[BaseStore | None, None]:
    if not settings.CANVAS_MEMORY_STORE_ENABLED:
        yield None
        return
    uri = settings.CHECKPOINTER_POSTGRES_URI
    if not uri:
        logger.warning("canvas.memory_store.skip", reason="missing CHECKPOINTER_POSTGRES_URI")
        yield None
        return

    index = {
        "dims": settings.ASSET_VECTOR_DIMENSION,
        "embed": _embed_texts,
    }
    await _ensure_store_base_schema(uri)
    await _ensure_store_vector_schema(uri, dims=settings.ASSET_VECTOR_DIMENSION)
    async with AsyncPostgresStore.from_conn_string(uri, index=index) as store:
        await store.setup()
        logger.info("canvas.memory_store.ready")
        yield store
