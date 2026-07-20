from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from tortoise.contrib.fastapi import RegisterTortoise

from app.chat.llm.model_catalog import try_refresh_model_catalog
from app.chat.skills.registry import SkillRegistry
from app.chat.turn.stale_cleanup import clear_stale_active_turns_on_startup
from app.core.checkpointer import create_checkpointer, set_chat_checkpointer
from app.core.database import db
from app.core.gateway import gateway_client
from app.core.logger import logger
from app.core.memory_store import create_memory_store, set_canvas_memory_store
from app.core.redis import redis_client


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    _ = app
    logger.info("Starting application")

    db_url = db.build_url()

    async with RegisterTortoise(
        app,
        db_url=db_url,
        modules={"models": ["app.models"]},
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
