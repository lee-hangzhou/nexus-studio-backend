from __future__ import annotations

import asyncio

from langchain_core.messages import AIMessage, HumanMessage
from langmem import create_memory_store_manager

from app.agent.chat.llm.gateway_chat_model import GatewayChatModel
from app.agent.chat.llm.model_catalog import model_catalog
from app.agent.chat.llm.registry import get_model_spec
from app.agent.runtime.background import background_supervisor
from app.agent.runtime.memory.instructions import FIXED_PROJECT_EXTRACT_INSTRUCTIONS
from app.agent.runtime.memory.registry import get_memory_domain
from app.agent.runtime.memory.schemas import ProjectFactMemory
from app.agent.runtime.memory.secrets import scan_text_for_secrets
from app.agent.runtime.memory_store import get_memory_store
from app.server.infra.config import settings
from app.server.infra.logger import logger


class MemoryExtractModelError(ValueError):
    """MEMORY_EXTRACT_MODEL 未在 gateway model catalog 中"""


def require_extract_model_key(model_key: str) -> str:
    """非空且在 gateway model catalog 才合法；禁止 synthetic 合成"""
    key = (model_key or "").strip()
    if not key:
        raise MemoryExtractModelError("MEMORY_EXTRACT_MODEL is empty")
    if not model_catalog.has(key):
        raise MemoryExtractModelError(
            f"MEMORY_EXTRACT_MODEL not in model catalog: {key}"
        )
    return key


def resolve_extract_model_key(*, turn_model_key: str | None = None) -> str | None:
    """优先 MEMORY_EXTRACT_MODEL；空则回落到本轮 turn 模型；皆空则关闭"""
    preferred = (settings.MEMORY_EXTRACT_MODEL or "").strip()
    if preferred:
        return require_extract_model_key(preferred)
    fallback = (turn_model_key or "").strip()
    if not fallback:
        return None
    return require_extract_model_key(fallback)


def validate_memory_extract_config() -> None:
    """启动期硬校验：显式配置了 MEMORY_EXTRACT_MODEL 则必须命中 catalog"""
    preferred = (settings.MEMORY_EXTRACT_MODEL or "").strip()
    if not preferred:
        return
    require_extract_model_key(preferred)


def schedule_canvas_memory_extract(
    *,
    user_text: str,
    answer_text: str,
    user_id: int,
    project_id: int,
    turn_id: str | None = None,
    turn_model_key: str | None = None,
) -> None:
    """仅用本轮 Human/AI 对入队项目记忆抽取；配置错误不拖垮 turn"""
    store = get_memory_store()
    if store is None or not settings.MEMORY_STORE_ENABLED:
        return
    try:
        model_key = resolve_extract_model_key(turn_model_key=turn_model_key)
    except MemoryExtractModelError as exc:
        logger.error(
            "canvas.memory.extract_config_invalid",
            error=str(exc),
            preferred=settings.MEMORY_EXTRACT_MODEL,
            turn_model_key=turn_model_key,
            turn_id=turn_id,
            user_id=user_id,
            project_id=project_id,
        )
        return
    if model_key is None:
        logger.info("canvas.memory.extract_skipped", reason="extract_model_empty")
        return

    if not get_memory_domain("canvas").scope_spec("project").extract_enabled:
        return

    user_text = (user_text or "").strip()
    answer_text = (answer_text or "").strip()
    if not answer_text:
        return
    hits = scan_text_for_secrets(f"{user_text}\n{answer_text}")
    if hits:
        logger.info(
            "canvas.memory.extract_skipped",
            reason="detect_secrets",
            turn_id=turn_id,
            user_id=user_id,
            project_id=project_id,
            secret_types=[h.secret_type for h in hits],
        )
        return

    serial_key = f"canvas.project:{user_id}:{project_id}"
    namespace = get_memory_domain("canvas").scope_spec("project").namespace

    async def _run() -> None:
        """执行一次项目记忆抽取"""
        try:
            async with asyncio.timeout(float(settings.MEMORY_EXTRACTION_TIMEOUT_SEC)):
                spec = get_model_spec(model_key)
                llm = GatewayChatModel(model_key=model_key, spec=spec)
                manager = create_memory_store_manager(
                    llm,
                    store=store,
                    namespace=namespace,
                    schemas=[ProjectFactMemory],
                    enable_inserts=True,
                    enable_deletes=True,
                    query_limit=5,
                    instructions=FIXED_PROJECT_EXTRACT_INSTRUCTIONS,
                )
                config = {
                    "configurable": {
                        "langgraph_user_id": str(user_id),
                        "project_id": str(project_id),
                    }
                }
                messages = [
                    HumanMessage(content=user_text),
                    AIMessage(content=answer_text),
                ]
                await manager.ainvoke({"messages": messages}, config=config)
                logger.info(
                    "canvas.memory.write",
                    source="background_extract",
                    user_id=user_id,
                    project_id=project_id,
                    turn_id=turn_id,
                    model_key=model_key,
                )
        except TimeoutError:
            logger.warning(
                "canvas.memory.extract_timeout",
                user_id=user_id,
                project_id=project_id,
                turn_id=turn_id,
            )
        except asyncio.CancelledError:
            logger.info(
                "canvas.memory.extract_cancelled",
                user_id=user_id,
                project_id=project_id,
                turn_id=turn_id,
            )
            raise
        except Exception as exc:
            logger.exception(
                "canvas.memory.background_failed",
                error=str(exc),
                user_id=user_id,
                project_id=project_id,
                turn_id=turn_id,
            )

    background_supervisor.start(
        _run(),
        name=f"canvas-memory-extract-{project_id}-{turn_id or 'na'}",
        serial_key=serial_key,
    )
