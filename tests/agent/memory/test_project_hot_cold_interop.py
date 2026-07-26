from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.store.memory import InMemoryStore
from langmem import create_memory_store_manager
from langmem import utils as langmem_utils
from langmem.knowledge.extraction import ExtractedMemory

from app.agent.runtime.memory.envelope import canonical_project_value, unwrap_store_content
from app.agent.runtime.memory.namespaces import CANVAS_PROJECT_MEMORY_NAMESPACE
from app.agent.runtime.memory.schemas import ProjectFactMemory


class _NoOpChatModel(BaseChatModel):
    """不发网的占位 ChatModel；manager 内 memory_manager 会被 mock"""

    @property
    def _llm_type(self) -> str:
        return "noop"

    def _generate(self, messages: list[Any], stop: list[str] | None = None, **kwargs: Any) -> ChatResult:
        """同步生成占位回复"""
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=""))])

    async def _agenerate(
        self, messages: list[Any], stop: list[str] | None = None, **kwargs: Any
    ) -> ChatResult:
        """异步生成占位回复"""
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=""))])


@pytest.mark.asyncio
async def test_canonical_manage_then_store_manager_reads_kind() -> None:
    """canonical aput 的 kind/content 必须能被 create_memory_store_manager 读取"""
    store = InMemoryStore()
    ns = ("canvas", "memory", "project", "1", "42", "records")
    payload = ProjectFactMemory(
        subject="女主",
        predicate="职业",
        object="急诊医生",
        context="本集设定",
    )
    key = str(uuid.uuid4())
    await store.aput(ns, key, canonical_project_value(payload))

    manager = create_memory_store_manager(
        _NoOpChatModel(),
        store=store,
        namespace=CANVAS_PROJECT_MEMORY_NAMESPACE,
        schemas=[ProjectFactMemory],
        enable_inserts=True,
        enable_deletes=True,
        query_limit=5,
    )

    async def _passthrough(payload: dict[str, Any], config: Any = None) -> list[ExtractedMemory]:
        """回传已有记忆，验证 manager 已成功解出 kind/content"""
        out: list[ExtractedMemory] = []
        for sid, kind, content in payload["existing"]:
            assert kind == "ProjectFactMemory"
            out.append(
                ExtractedMemory(
                    id=sid,
                    content=ProjectFactMemory.model_validate(content),
                )
            )
        return out

    manager.memory_manager.ainvoke = AsyncMock(side_effect=_passthrough)

    await manager.ainvoke(
        {
            "messages": [
                HumanMessage(content="女主是急诊医生"),
                AIMessage(content="已记下。"),
            ]
        },
        config={
            "configurable": {
                "langgraph_user_id": "1",
                "project_id": "42",
            }
        },
    )
    manager.memory_manager.ainvoke.assert_awaited()
    item = await store.aget(ns, key)
    assert item is not None
    assert item.value["kind"] == "ProjectFactMemory"
    assert unwrap_store_content(item.value)["object"] == "急诊医生"

    # 无 kind 的旧写法会在 manager 读路径 KeyError
    bad_key = str(uuid.uuid4())
    await store.aput(
        ns,
        bad_key,
        {"content": payload.model_dump(mode="json")},
    )
    with pytest.raises(KeyError):
        await manager.ainvoke(
            {
                "messages": [
                    HumanMessage(content="女主是急诊医生"),
                    AIMessage(content="已记下。"),
                ]
            },
            config={
                "configurable": {
                    "langgraph_user_id": "1",
                    "project_id": "42",
                }
            },
        )


@pytest.mark.asyncio
async def test_namespace_template_includes_records_terminator() -> None:
    """解析后的项目 namespace 必须以 records 结尾"""
    namespacer = langmem_utils.NamespaceTemplate(CANVAS_PROJECT_MEMORY_NAMESPACE)
    ns = namespacer(
        {
            "configurable": {
                "langgraph_user_id": "1",
                "project_id": "10",
            }
        }
    )
    assert ns[-1] == "records"
    assert ns == ("canvas", "memory", "project", "1", "10", "records")
