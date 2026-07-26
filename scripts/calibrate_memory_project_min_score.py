#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import math
import uuid

from langgraph.store.base.embed import get_text_at_path
from langgraph.store.memory import InMemoryStore

from app.agent.runtime.memory.envelope import canonical_project_value
from app.agent.runtime.memory.schemas import ProjectFactMemory
from app.server.infra.config import settings
from app.server.infra.embeddings import gateway_embeddings_client

# (query, fact) — fact 经 canonical 信封后索引 fields=["content"]
POS = [
    (
        "把女主改成冷静的医生",
        ProjectFactMemory(subject="女主", predicate="职业", object="急诊医生", context="本集设定"),
    ),
    (
        "延续上一版赛博朋克雨夜街头",
        ProjectFactMemory(subject="视觉", predicate="基调", object="赛博朋克雨夜", context=""),
    ),
    (
        "反派不要再出现在这集",
        ProjectFactMemory(subject="本集", predicate="不出现", object="反派 Boss", context=""),
    ),
    (
        "配色继续用上次的冷青",
        ProjectFactMemory(subject="项目", predicate="主色", object="冷青", context=""),
    ),
]
NEG = [
    (
        "脚踝有点疼怎么构图更稳",
        ProjectFactMemory(subject="用户", predicate="职业", object="程序员", context=""),
    ),
    (
        "这张海报字号加大",
        ProjectFactMemory(subject="用户", predicate="爱好", object="打篮球", context=""),
    ),
    (
        "导出当前节点资产",
        ProjectFactMemory(subject="女主", predicate="爱好", object="喝茶", context=""),
    ),
    (
        "检查生成任务状态",
        ProjectFactMemory(subject="用户", predicate="回复偏好", object="简洁", context=""),
    ),
]


def _content_index_text(fact: ProjectFactMemory) -> str:
    """与 Store fields=['content'] 抽出的索引文本一致"""
    texts = get_text_at_path(canonical_project_value(fact), "content")
    if len(texts) != 1:
        raise RuntimeError(f"expected one content text, got {len(texts)}")
    return texts[0]


def _cosine(a: list[float], b: list[float]) -> float:
    """与 InMemoryStore._cosine_similarity 同口径"""
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


async def main() -> None:
    """批量 embedding 后按 Store content 索引算 asearch 同口径分数"""
    pairs = POS + NEG
    queries = [q for q, _ in pairs]
    contents = [_content_index_text(m) for _, m in pairs]
    # 一次批量，避免逐条超时；超时放宽到 120s
    vectors = await gateway_embeddings_client.embed_texts_batch(
        queries + contents,
        model=settings.MEMORY_EMBEDDING_MODEL,
        dimensions=settings.MEMORY_VECTOR_DIMENSION,
        timeout_sec=120.0,
    )
    n = len(pairs)
    q_vecs = vectors[:n]
    c_vecs = vectors[n:]
    pos_scores = [_cosine(q_vecs[i], c_vecs[i]) for i in range(len(POS))]
    neg_scores = [_cosine(q_vecs[len(POS) + i], c_vecs[len(POS) + i]) for i in range(len(NEG))]

    # 用 InMemoryStore 抽检第一条，确认 score 与手工 cosine 一致
    cache = {queries[0]: q_vecs[0], contents[0]: c_vecs[0]}

    async def _embed(texts: list[str]) -> list[list[float]]:
        """从预计算缓存取向量"""
        return [cache[t] for t in texts]

    store = InMemoryStore(
        index={
            "dims": settings.MEMORY_VECTOR_DIMENSION,
            "embed": _embed,
            "fields": ["content"],
            "distance_type": "cosine",
        }
    )
    ns = ("calibrate", "project", "records")
    await store.aput(ns, str(uuid.uuid4()), canonical_project_value(POS[0][1]))
    hits = await store.asearch(ns, query=POS[0][0], limit=1)
    store_score = float(hits[0].score) if hits and hits[0].score is not None else None
    if store_score is None or abs(store_score - pos_scores[0]) > 1e-5:
        raise RuntimeError(
            f"store score mismatch: store={store_score} cosine={pos_scores[0]}"
        )

    min_pos = min(pos_scores)
    max_neg = max(neg_scores)
    if min_pos > max_neg:
        threshold = (min_pos + max_neg) / 2
    else:
        threshold = max_neg + 1e-6
    print(
        {
            "pos_scores": [round(s, 6) for s in pos_scores],
            "neg_scores": [round(s, 6) for s in neg_scores],
            "min_pos": round(min_pos, 6),
            "max_neg": round(max_neg, 6),
            "store_spot_check": round(store_score, 6),
            "MEMORY_PROJECT_INJECT_MIN_SCORE": round(threshold, 4),
            "model": settings.MEMORY_EMBEDDING_MODEL,
            "index_fields": ["content"],
            "index_text_sample": contents[0][:120],
        }
    )


if __name__ == "__main__":
    asyncio.run(main())
