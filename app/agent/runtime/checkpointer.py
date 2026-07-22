from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from app.server.infra.config import CheckpointerType, settings
from app.agent.runtime.runtime import runtime_resources


def get_chat_checkpointer() -> BaseCheckpointSaver:
    if runtime_resources.chat_checkpointer is None:
        raise RuntimeError("Chat checkpointer is not initialized; application lifespan may not have started")
    return runtime_resources.chat_checkpointer


def set_chat_checkpointer(saver: BaseCheckpointSaver | None) -> None:
    runtime_resources.chat_checkpointer = saver


@asynccontextmanager
async def create_checkpointer() -> AsyncGenerator[BaseCheckpointSaver, None]:
    """Create a LangGraph checkpointer (SQLite or Postgres per settings)."""

    if settings.CHECKPOINTER_TYPE == CheckpointerType.SQLITE:
        async with AsyncSqliteSaver.from_conn_string(settings.CHECKPOINTER_SQLITE_PATH) as saver:
            yield saver
            return
    if settings.CHECKPOINTER_TYPE == CheckpointerType.POSTGRES:
        if settings.CHECKPOINTER_POSTGRES_URI is None:
            raise RuntimeError("PostgreSQL checkpointer 缺少连接地址")

        async with AsyncPostgresSaver.from_conn_string(settings.CHECKPOINTER_POSTGRES_URI) as saver:
            await saver.setup()
            yield saver
            return

    raise RuntimeError(f"不支持的 checkpointer 类型: {settings.CHECKPOINTER_TYPE}")
