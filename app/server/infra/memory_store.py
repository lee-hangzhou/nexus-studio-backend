from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

import psycopg
from langgraph.store.base import BaseStore
from langgraph.store.postgres import AsyncPostgresStore, PostgresStore

from app.server.infra.config import settings
from app.server.infra.embeddings import gateway_embeddings_client
from app.server.infra.logger import logger

_REQUIRED_STORE_COLUMNS = frozenset(
    {"prefix", "key", "value", "created_at", "updated_at", "expires_at", "ttl_minutes"}
)
_REQUIRED_VECTOR_COLUMNS = frozenset(
    {"prefix", "key", "field_name", "embedding", "created_at", "updated_at"}
)


class MemoryStoreSchemaError(RuntimeError):
    """Store 表结构与官方不一致，或缺少 ON DELETE CASCADE"""


async def _embed_texts(texts: list[str]) -> list[list[float]]:
    """经网关为 Store 索引生成 embedding"""
    if not texts:
        return []
    return await gateway_embeddings_client.embed_texts_batch(
        texts,
        model=settings.MEMORY_EMBEDDING_MODEL,
        dimensions=settings.MEMORY_VECTOR_DIMENSION,
        timeout_sec=float(settings.MEMORY_TOOL_TIMEOUT_SEC),
    )


def _sync_setup(uri: str) -> None:
    """官方 sync PostgresStore.setup，flat ANN（高维跳过 HNSW）"""
    index = {
        "dims": settings.MEMORY_VECTOR_DIMENSION,
        "embed": _embed_texts,
        "fields": ["content"],
        "distance_type": "cosine",
        "ann_index_config": {"kind": "flat"},
    }
    with PostgresStore.from_conn_string(uri, index=index) as store:
        store.setup()


async def _table_columns(cur: Any, table: str) -> set[str]:
    """读取 public 表列名集合"""
    await cur.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = %s
        """,
        (table,),
    )
    rows = await cur.fetchall()
    return {str(r[0]) for r in rows}


async def _assert_store_schema_healthy(uri: str) -> None:
    """缺表、列不齐或 store_vectors 无 CASCADE FK 时拒启"""
    async with await psycopg.AsyncConnection.connect(uri) as conn:
        async with conn.cursor() as cur:
            store_cols = await _table_columns(cur, "store")
            vector_cols = await _table_columns(cur, "store_vectors")
            if not store_cols or not vector_cols:
                raise MemoryStoreSchemaError(
                    "memory store tables missing after setup (store/store_vectors)"
                )
            missing_store = _REQUIRED_STORE_COLUMNS - store_cols
            missing_vectors = _REQUIRED_VECTOR_COLUMNS - vector_cols
            if missing_store or missing_vectors:
                raise MemoryStoreSchemaError(
                    "memory store schema columns mismatch official structure: "
                    f"store_missing={sorted(missing_store)} "
                    f"vectors_missing={sorted(missing_vectors)}"
                )
            await cur.execute(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM pg_constraint c
                    JOIN pg_class rel ON rel.oid = c.conrelid
                    JOIN pg_namespace nsp ON nsp.oid = rel.relnamespace
                    WHERE nsp.nspname = 'public'
                      AND rel.relname = 'store_vectors'
                      AND c.contype = 'f'
                      AND c.confdeltype = 'c'
                      AND pg_get_constraintdef(c.oid) ILIKE '%REFERENCES store%'
                )
                """
            )
            has_cascade = bool((await cur.fetchone())[0])
            if not has_cascade:
                raise MemoryStoreSchemaError(
                    "store_vectors missing ON DELETE CASCADE FK to store; "
                    "refuse to start (no silent repair). Rebuild Store schema "
                    "only with explicit authorization."
                )


@asynccontextmanager
async def create_memory_store() -> AsyncGenerator[BaseStore | None, None]:
    """创建 AsyncPostgresStore；MEMORY_STORE_ENABLED=false 时 yield None"""
    if not settings.MEMORY_STORE_ENABLED:
        yield None
        return
    uri = settings.CHECKPOINTER_POSTGRES_URI
    if not uri:
        raise RuntimeError(
            "MEMORY_STORE_ENABLED=true but CHECKPOINTER_POSTGRES_URI is empty"
        )

    await asyncio.to_thread(_sync_setup, uri)
    await _assert_store_schema_healthy(uri)

    index = {
        "dims": settings.MEMORY_VECTOR_DIMENSION,
        "embed": _embed_texts,
        "fields": ["content"],
        "distance_type": "cosine",
        "ann_index_config": {"kind": "flat"},
    }
    async with AsyncPostgresStore.from_conn_string(uri, index=index) as store:
        logger.info("memory.store.ready", dims=settings.MEMORY_VECTOR_DIMENSION)
        yield store
