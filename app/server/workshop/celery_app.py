"""Celery：Beat 到期派发 + Worker 执行 workflow_run。"""

from __future__ import annotations

import asyncio
from typing import Any

from celery import Celery
from celery.signals import worker_process_init

from app.server.infra.config import settings

celery_app = Celery(
    "nexus_studio_workshop",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
)

celery_app.conf.update(
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    beat_schedule={
        "workshop-dispatch-due-schedules": {
            "task": "workshop.dispatch_due_schedules",
            "schedule": 60.0,
        }
    },
)


@worker_process_init.connect
def _init_worker_process(**_kwargs: Any) -> None:
    """prefork 子进程对齐 API lifespan：拉取模型能力目录进内存"""
    from app.agent.chat.llm.model_catalog import try_refresh_model_catalog

    asyncio.run(try_refresh_model_catalog())


def enqueue_workflow_run(project_id: str, user_id: int, run_id: str) -> None:
    """入队执行一条工作流运行记录"""
    celery_app.send_task(
        "workshop.execute_workflow_run",
        kwargs={
            "project_id": project_id,
            "user_id": user_id,
            "run_id": run_id,
        },
        task_id=_celery_task_id(project_id, run_id),
    )


def revoke_workflow_run(project_id: str, user_id: int, run_id: str) -> None:
    """尽力撤销未执行完的 Celery 任务（不保证中断已在跑的专家推理）"""
    del user_id  # 签名与 enqueue 对齐，便于服务回调注入
    celery_app.control.revoke(
        _celery_task_id(project_id, run_id),
        terminate=False,
    )


def _celery_task_id(project_id: str, run_id: str) -> str:
    """稳定 task_id，便于 revoke"""
    return f"workshop-run-{project_id}-{run_id}"


@celery_app.task(name="workshop.execute_workflow_run")
def execute_workflow_run_task(
    *, project_id: str, user_id: int, run_id: str
) -> dict[str, Any]:
    """Worker：执行一条运行记录"""
    return asyncio.run(_execute_run(project_id, user_id, run_id))


@celery_app.task(name="workshop.dispatch_due_schedules")
def dispatch_due_schedules_task() -> dict[str, Any]:
    """Beat：扫描到期定时并入队"""
    return asyncio.run(_dispatch_due())


async def _ensure_model_catalog() -> None:
    """目录为空时再拉一次，避免进程初始化时网关未就绪导致永久空缓存"""
    from app.agent.chat.llm.model_catalog import model_catalog, try_refresh_model_catalog

    if model_catalog.known_model_ids():
        return
    await try_refresh_model_catalog()


async def _execute_run(project_id: str, user_id: int, run_id: str) -> dict[str, Any]:
    """异步执行入口：对齐 API lifespan 的 checkpointer（Celery 进程无 FastAPI lifespan）"""
    from app.agent.runtime.checkpointer import (
        create_checkpointer,
        set_chat_checkpointer,
    )
    from app.agent.runtime.memory_store import set_memory_store
    from app.composition import workshop_run_execution_service
    from app.server.infra.database import db
    from app.server.infra.memory_store import create_memory_store

    async with create_checkpointer() as checkpointer, create_memory_store() as memory_store:
        set_chat_checkpointer(checkpointer)
        set_memory_store(memory_store)
        await db.connect()
        try:
            await _ensure_model_catalog()
            status = await workshop_run_execution_service.execute_run(
                project_id=project_id, user_id=user_id, run_id=run_id
            )
            return {"run_id": run_id, "status": status.value}
        finally:
            await db.disconnect()
            set_chat_checkpointer(None)
            set_memory_store(None)


async def _dispatch_due() -> dict[str, Any]:
    """异步扫描到期 schedule"""
    from datetime import datetime, timezone

    from app.composition import workshop_workflow_schedule_service
    from app.server.infra.database import db

    await db.connect()
    try:
        results = await workshop_workflow_schedule_service.tick_once(
            now=datetime.now(timezone.utc),
            batch_size=settings.WORKSHOP_SCHEDULE_TICK_BATCH_SIZE,
        )
        return {"claimed": len(results)}
    finally:
        await db.disconnect()


if __name__ == "__main__":
    import sys

    # macOS + prefork 会与 objc 运行时冲突（SIGABRT: initializeAfterForkError），
    # PyCharm 调试器叠 fork 更易炸。本地 Darwin 用 solo；Linux 仍用 prefork。
    if sys.platform == "darwin":
        pool_args = ["--pool=solo"]
    else:
        pool_args = ["--pool=prefork", "--concurrency=1"]
    celery_app.worker_main(
        argv=[
            "worker",
            "--beat",
            "--loglevel=INFO",
            *pool_args,
        ]
    )
