from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agent.runtime.memory.inject import MemoryInjectionRequest, build_memory_injection
from app.agent.runtime.memory.schemas import ProjectFactMemory, UserMemory


def _item(key: str, value: dict, score: float | None = None) -> SimpleNamespace:
    return SimpleNamespace(key=key, value=value, score=score)


@pytest.mark.asyncio
async def test_user_queryless_inject_and_memory_label(monkeypatch: pytest.MonkeyPatch) -> None:
    store = MagicMock()
    store.asearch = AsyncMock(
        return_value=[
            _item(
                "a",
                {"content": UserMemory(statement="回复请用中文", context="").model_dump()},
            )
        ]
    )
    monkeypatch.setattr(
        "app.agent.runtime.memory.inject.settings.MEMORY_USER_INJECT_FETCH_LIMIT",
        32,
    )
    monkeypatch.setattr(
        "app.agent.runtime.memory.inject.settings.MEMORY_INJECTION_TIMEOUT_SEC",
        2,
    )
    result = await build_memory_injection(
        MemoryInjectionRequest(
            domain="chat",
            user_id=1,
            user_message="今天天气怎么样",
            is_resume=False,
            memory_tools_enabled=True,
            store=store,
        )
    )
    assert "source: memory" in result.memory_blocks_text
    assert "authority: low" in result.memory_blocks_text
    assert "回复请用中文" in result.memory_blocks_text
    assert result.ops_brief_text is not None
    # queryless user fetch
    assert store.asearch.await_args.kwargs.get("query") is None


@pytest.mark.asyncio
async def test_project_min_score_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    store = MagicMock()

    async def asearch(ns, query=None, limit=5):
        if "user" in ns:
            return []
        return [
            _item(
                "low",
                {
                    "kind": "ProjectFactMemory",
                    "content": ProjectFactMemory(
                        subject="用户",
                        predicate="职业",
                        object="程序员",
                        context="",
                    ).model_dump(),
                },
                score=0.1,
            ),
            _item(
                "high",
                {
                    "kind": "ProjectFactMemory",
                    "content": ProjectFactMemory(
                        subject="女主",
                        predicate="职业",
                        object="医生",
                        context="",
                    ).model_dump(),
                },
                score=0.9,
            ),
        ]

    store.asearch = AsyncMock(side_effect=asearch)
    monkeypatch.setattr(
        "app.agent.runtime.memory.inject.settings.MEMORY_PROJECT_INJECT_MIN_SCORE",
        0.35,
    )
    monkeypatch.setattr(
        "app.agent.runtime.memory.inject.settings.MEMORY_INJECTION_TIMEOUT_SEC",
        2,
    )
    result = await build_memory_injection(
        MemoryInjectionRequest(
            domain="canvas",
            user_id=1,
            project_id=42,
            user_message="把女主改成冷静的医生",
            is_resume=False,
            memory_tools_enabled=False,
            store=store,
        )
    )
    assert "医生" in result.memory_blocks_text
    assert "程序员" not in result.memory_blocks_text


@pytest.mark.asyncio
async def test_project_missing_score_is_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    store = MagicMock()

    async def asearch(ns, query=None, limit=5):
        if "user" in ns:
            return []
        return [
            _item(
                "noscore",
                {
                    "kind": "ProjectFactMemory",
                    "content": ProjectFactMemory(
                        subject="女主",
                        predicate="职业",
                        object="医生",
                        context="",
                    ).model_dump(),
                },
                score=None,
            ),
        ]

    store.asearch = AsyncMock(side_effect=asearch)
    monkeypatch.setattr(
        "app.agent.runtime.memory.inject.settings.MEMORY_PROJECT_INJECT_MIN_SCORE",
        0.35,
    )
    monkeypatch.setattr(
        "app.agent.runtime.memory.inject.settings.MEMORY_INJECTION_TIMEOUT_SEC",
        2,
    )
    result = await build_memory_injection(
        MemoryInjectionRequest(
            domain="canvas",
            user_id=1,
            project_id=42,
            user_message="把女主改成冷静的医生",
            is_resume=False,
            memory_tools_enabled=False,
            store=store,
        )
    )
    assert "医生" not in result.memory_blocks_text


@pytest.mark.asyncio
async def test_resume_skips_project(monkeypatch: pytest.MonkeyPatch) -> None:
    store = MagicMock()
    store.asearch = AsyncMock(return_value=[])
    monkeypatch.setattr(
        "app.agent.runtime.memory.inject.settings.MEMORY_INJECTION_TIMEOUT_SEC",
        2,
    )
    await build_memory_injection(
        MemoryInjectionRequest(
            domain="canvas",
            user_id=1,
            project_id=42,
            user_message="继续",
            is_resume=True,
            memory_tools_enabled=False,
            store=store,
        )
    )
    # Only user namespace searched
    assert store.asearch.await_count == 1
    ns = store.asearch.await_args.args[0]
    assert "user" in ns


@pytest.mark.asyncio
async def test_canvas_missing_project_id_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    store = MagicMock()
    store.asearch = AsyncMock(return_value=[])
    monkeypatch.setattr(
        "app.agent.runtime.memory.inject.settings.MEMORY_INJECTION_TIMEOUT_SEC",
        2,
    )
    with pytest.raises(ValueError, match="project_id"):
        await build_memory_injection(
            MemoryInjectionRequest(
                domain="canvas",
                user_id=1,
                project_id=None,
                user_message="hi",
                is_resume=False,
                memory_tools_enabled=False,
                store=store,
            )
        )


@pytest.mark.asyncio
async def test_store_failure_does_not_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    store = MagicMock()
    store.asearch = AsyncMock(side_effect=RuntimeError("embed down"))
    monkeypatch.setattr(
        "app.agent.runtime.memory.inject.settings.MEMORY_INJECTION_TIMEOUT_SEC",
        2,
    )
    result = await build_memory_injection(
        MemoryInjectionRequest(
            domain="chat",
            user_id=1,
            user_message="hi",
            is_resume=False,
            memory_tools_enabled=False,
            store=store,
        )
    )
    assert result.memory_blocks_text == ""
