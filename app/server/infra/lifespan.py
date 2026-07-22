from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from tortoise.contrib.fastapi import RegisterTortoise

from app.agent.chat.llm.model_catalog import try_refresh_model_catalog
from app.agent.chat.skills.registry import SkillRegistry
from app.agent.chat.turn.stale_cleanup import clear_stale_active_turns_on_startup
from app.agent.runtime.checkpointer import create_checkpointer, set_chat_checkpointer
from app.agent.runtime.memory_store import create_memory_store, set_canvas_memory_store
from app.server.infra.database import db
from app.server.infra.gateway import gateway_client
from app.server.infra.logger import logger
from app.server.infra.redis import redis_client
from app.server.persistence import TORTOISE_ORM_MODEL_MODULES


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    _ = app
    logger.info("Starting application")

    db_url = db.build_url()

    async with RegisterTortoise(
        app,
        db_url=db_url,
        modules={"models": list(TORTOISE_ORM_MODEL_MODULES)},
        use_tz=True,
        generate_schemas=False,
    ):
        db.mark_initialized()
        logger.info("Database connected")

        await redis_client.connect()
        logger.info("Redis connected")

        await clear_stale_active_turns_on_startup()

        skills = SkillRegistry.load()
        logger.info("SkillRegistry loaded", skill_count=len(skills), skill_names=[s.name for s in skills])

        await try_refresh_model_catalog()

        async with create_checkpointer() as checkpointer, create_memory_store() as memory_store:
            set_chat_checkpointer(checkpointer)
            set_canvas_memory_store(memory_store)
            logger.info(
                "LangGraph checkpointer initialized for chat",
                canvas_memory_store=memory_store is not None,
            )
            try:
                yield
            finally:
                set_chat_checkpointer(None)
                set_canvas_memory_store(None)
                await gateway_client.close()
                await redis_client.disconnect()
                db.mark_shutdown()

    logger.info("Shutdown completed")
