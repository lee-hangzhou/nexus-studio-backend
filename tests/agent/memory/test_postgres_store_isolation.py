from __future__ import annotations

import os
import uuid

import psycopg
import pytest
from langgraph.store.postgres import AsyncPostgresStore, PostgresStore

from app.server.infra.memory_store import MemoryStoreSchemaError, _assert_store_schema_healthy

URI = os.environ.get("TEST_MEMORY_POSTGRES_URI", "").strip()
pytestmark = pytest.mark.skipif(not URI, reason="TEST_MEMORY_POSTGRES_URI not set")


def _fake_embed(texts: list[str]) -> list[list[float]]:
    """测试用确定性单位向量"""
    out = []
    for i, _ in enumerate(texts):
        vec = [0.0] * 8
        vec[i % 8] = 1.0
        out.append(vec)
    return out


def _setup_official() -> None:
    """用官方 flat setup 重建测试库 Store 表"""
    index = {
        "dims": 8,
        "embed": _fake_embed,
        "fields": ["content"],
        "distance_type": "cosine",
        "ann_index_config": {"kind": "flat"},
    }
    with PostgresStore.from_conn_string(URI, index=index) as store:
        store.setup()


@pytest.mark.asyncio
async def test_namespace_id_prefix_isolation_1_10_100() -> None:
    """用户 id 1/10/100 在 LIKE 前缀下不得串读"""
    await __import__("asyncio").to_thread(_setup_official)
    await _assert_store_schema_healthy(URI)

    index = {
        "dims": 8,
        "embed": _fake_embed,
        "fields": ["content"],
        "distance_type": "cosine",
        "ann_index_config": {"kind": "flat"},
    }
    async with AsyncPostgresStore.from_conn_string(URI, index=index) as store:
        for uid in ("1", "10", "100"):
            ns = ("canvas", "memory", "user", uid, "records")
            await store.aput(
                ns,
                str(uuid.uuid4()),
                {"content": {"statement": f"user-{uid}", "context": ""}},
            )
        for uid in ("1", "10", "100"):
            ns = ("canvas", "memory", "user", uid, "records")
            items = await store.asearch(ns, query=None, limit=50)
            statements = [
                (it.value.get("content") or {}).get("statement")
                if isinstance(it.value.get("content"), dict)
                else None
                for it in items
            ]
            markers = [s for s in statements if isinstance(s, str) and s.startswith("user-")]
            assert markers == [f"user-{uid}"]


@pytest.mark.asyncio
async def test_old_schema_without_cascade_fails_closed() -> None:
    """真实 store_vectors 缺 CASCADE FK 时 _assert_store_schema_healthy 必须抛错"""
    await __import__("asyncio").to_thread(_setup_official)
    await _assert_store_schema_healthy(URI)

    async with await psycopg.AsyncConnection.connect(URI) as conn:
        async with conn.cursor() as cur:
            await cur.execute("DROP TABLE IF EXISTS store_vectors CASCADE")
            await cur.execute(
                """
                CREATE TABLE store_vectors (
                    prefix text NOT NULL,
                    key text NOT NULL,
                    field_name text NOT NULL,
                    embedding vector(8),
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (prefix, key, field_name)
                )
                """
            )
            await conn.commit()

    with pytest.raises(MemoryStoreSchemaError, match="CASCADE"):
        await _assert_store_schema_healthy(URI)

    # 恢复官方结构，避免污染同库后续测试
    await __import__("asyncio").to_thread(_setup_official)
    await _assert_store_schema_healthy(URI)


@pytest.mark.asyncio
async def test_column_mismatch_fails_closed() -> None:
    """列不齐时拒启"""
    await __import__("asyncio").to_thread(_setup_official)
    async with await psycopg.AsyncConnection.connect(URI) as conn:
        async with conn.cursor() as cur:
            await cur.execute("ALTER TABLE store DROP COLUMN IF EXISTS ttl_minutes")
            await conn.commit()
    with pytest.raises(MemoryStoreSchemaError, match="columns mismatch"):
        await _assert_store_schema_healthy(URI)
    await __import__("asyncio").to_thread(_setup_official)
    await _assert_store_schema_healthy(URI)
