"""Celery worker 常驻 asyncio loop：禁止 asyncio.run 关 loop。"""

from __future__ import annotations

import asyncio

import pytest

from app.server.workshop.worker_loop import (
    get_worker_loop,
    init_worker_loop,
    reset_worker_loop_for_tests,
    run_in_worker_loop,
)


@pytest.fixture(autouse=True)
def _clean_worker_loop() -> None:
    reset_worker_loop_for_tests()
    yield
    reset_worker_loop_for_tests()


def test_get_worker_loop_fails_closed_before_init() -> None:
    with pytest.raises(RuntimeError, match="not initialized"):
        get_worker_loop()


def test_run_in_worker_loop_reuses_same_open_loop() -> None:
    loop = init_worker_loop()

    async def probe() -> int:
        return id(asyncio.get_running_loop())

    first = run_in_worker_loop(probe())
    second = run_in_worker_loop(probe())

    assert first == second == id(loop)
    assert not loop.is_closed()
    assert get_worker_loop() is loop


def test_run_in_worker_loop_keeps_loop_usable_after_httpx_client_cycle() -> None:
    """回归：同一 AsyncClient 跨两次任务不因 loop 关闭而炸。"""
    import httpx

    init_worker_loop()
    client = httpx.AsyncClient()

    async def ping_once() -> int:
        # 不发真实网络：只走一次 aclose 路径上的 loop 绑定
        transport = client._transport
        assert transport is not None
        return id(asyncio.get_running_loop())

    first = run_in_worker_loop(ping_once())
    second = run_in_worker_loop(ping_once())
    assert first == second
    assert not get_worker_loop().is_closed()

    async def close_client() -> None:
        await client.aclose()

    run_in_worker_loop(close_client())
    assert not get_worker_loop().is_closed()
