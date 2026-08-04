"""Celery worker 子进程常驻 asyncio event loop。

与 FastAPI lifespan 同构：一进程一 loop，进程级 httpx.AsyncClient 等资源绑在该 loop 上。
禁止在 worker 任务路径使用 asyncio.run()——它会关闭 loop，导致后续任务
RuntimeError: Event loop is closed。
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import TypeVar

T = TypeVar("T")

_loop: asyncio.AbstractEventLoop | None = None


def init_worker_loop() -> asyncio.AbstractEventLoop:
    """prefork/solo 子进程启动时创建并安装常驻 loop。"""
    global _loop
    if _loop is not None and not _loop.is_closed():
        return _loop
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    _loop = loop
    return loop


def get_worker_loop() -> asyncio.AbstractEventLoop:
    """返回常驻 loop；未初始化或已关闭则 fail closed。"""
    if _loop is None or _loop.is_closed():
        raise RuntimeError(
            "celery worker event loop is not initialized; "
            "worker_process_init must call init_worker_loop()"
        )
    return _loop


def run_in_worker_loop(coro: Coroutine[object, object, T]) -> T:
    """在常驻 loop 上执行协程，执行后不关闭 loop。"""
    return get_worker_loop().run_until_complete(coro)


def reset_worker_loop_for_tests() -> None:
    """测试隔离：关闭并清空常驻 loop。"""
    global _loop
    if _loop is not None and not _loop.is_closed():
        _loop.close()
    _loop = None
    asyncio.set_event_loop(None)
