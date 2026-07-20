"""Long-lived Playwright run-server container per conversation (wave3)."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.core.logger import logger
from app.core.redis import redis_client

_CONTAINER_NAME_PREFIX = "dream-drama-browser-"
CONTAINER_WORKSPACE_ROOT = "/workspace"


def _redis_key(conversation_id: int) -> str:
    return f"chat:browser_session:{conversation_id}"


@dataclass(frozen=True)
class BrowserSessionRecord:
    container_id: str
    ws_endpoint_in_container: str
    ws_endpoint_host: str
    driver_endpoint_in_container: str
    driver_endpoint_host: str
    idle_deadline: float


def _container_name(conversation_id: int) -> str:
    return f"{_CONTAINER_NAME_PREFIX}{conversation_id}"


def _loopback_publish_spec(container_port: int) -> str:
    return f"127.0.0.1:0:{container_port}"


def _record_from_dict(conversation_id: int, data: dict[str, Any]) -> BrowserSessionRecord | None:
    container_id = str(data.get("container_id") or "")
    idle_deadline = float(data.get("idle_deadline") or 0)
    in_container = str(data.get("ws_endpoint_in_container") or "")
    host = str(data.get("ws_endpoint_host") or "")
    driver_in = str(data.get("driver_endpoint_in_container") or "")
    driver_host = str(data.get("driver_endpoint_host") or "")
    if container_id and in_container and host and driver_in and driver_host:
        # Records created before the backend was containerized used loopback for
        # these fields. Loopback points back at the backend container, so rebuild
        # the Docker-network endpoints from the deterministic child name.
        name = _container_name(conversation_id)
        if in_container.startswith(("ws://127.0.0.1:", "ws://localhost:")):
            in_container = f"ws://{name}:{settings.CHAT_BROWSER_RUN_SERVER_PORT}/"
        if driver_in.startswith(("http://127.0.0.1:", "http://localhost:")):
            driver_in = f"http://{name}:{settings.CHAT_BROWSER_DRIVER_PORT}"
        return BrowserSessionRecord(
            container_id=container_id,
            ws_endpoint_in_container=in_container,
            ws_endpoint_host=host,
            driver_endpoint_in_container=driver_in,
            driver_endpoint_host=driver_host,
            idle_deadline=idle_deadline,
        )
    return None


async def _load_record(conversation_id: int) -> BrowserSessionRecord | None:
    raw = await redis_client.get(_redis_key(conversation_id))
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    record = _record_from_dict(conversation_id, data)
    if record is not None:
        return record
    if data.get("ws_endpoint") or data.get("active_page_url"):
        logger.warning(
            "browser.session.legacy_record_cleared",
            conversation_id=conversation_id,
        )
        await clear_record(conversation_id)
    return None


async def _save_record(conversation_id: int, record: BrowserSessionRecord) -> None:
    payload: dict[str, Any] = {
        "container_id": record.container_id,
        "ws_endpoint_in_container": record.ws_endpoint_in_container,
        "ws_endpoint_host": record.ws_endpoint_host,
        "driver_endpoint_in_container": record.driver_endpoint_in_container,
        "driver_endpoint_host": record.driver_endpoint_host,
        "idle_deadline": record.idle_deadline,
    }
    await redis_client.set(
        _redis_key(conversation_id),
        json.dumps(payload, ensure_ascii=False),
        ex=max(60, int(settings.CHAT_BROWSER_IDLE_SEC)),
    )


async def clear_record(conversation_id: int) -> None:
    await redis_client.delete(_redis_key(conversation_id))


async def _container_running(container_id: str) -> bool:
    proc = await asyncio.create_subprocess_exec(
        "docker",
        "inspect",
        "-f",
        "{{.State.Running}}",
        container_id,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    return proc.returncode == 0 and stdout.decode().strip().lower() == "true"


async def _remove_container(name: str) -> None:
    proc = await asyncio.create_subprocess_exec(
        "docker",
        "rm",
        "-f",
        name,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await proc.communicate()


async def _mapped_host_port(container_id: str, container_port: int) -> int:
    proc = await asyncio.create_subprocess_exec(
        "docker",
        "port",
        container_id,
        f"{container_port}/tcp",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        detail = stderr.decode(errors="replace") or stdout.decode(errors="replace")
        raise RuntimeError(f"docker port failed: {detail}")
    line = stdout.decode(errors="replace").strip().split("\n")[0]
    if not line or ":" not in line:
        raise RuntimeError(f"unexpected docker port output: {line!r}")
    return int(line.rsplit(":", 1)[-1])


async def _start_container(conversation_id: int, workspace: Path) -> BrowserSessionRecord:
    from app.chat.browser import container_client

    name = _container_name(conversation_id)
    await _remove_container(name)
    workspace.mkdir(parents=True, exist_ok=True)
    run_port = settings.CHAT_BROWSER_RUN_SERVER_PORT
    driver_port = settings.CHAT_BROWSER_DRIVER_PORT
    cmd = [
        "docker",
        "run",
        "-d",
        "--name",
        name,
        "--network",
        settings.CHAT_BROWSER_DOCKER_NETWORK,
        "-p",
        _loopback_publish_spec(run_port),
        "-p",
        _loopback_publish_spec(driver_port),
        "-v",
        f"{workspace.resolve()}:{CONTAINER_WORKSPACE_ROOT}",
        "-w",
        CONTAINER_WORKSPACE_ROOT,
        "-e",
        f"CHAT_BROWSER_RUN_SERVER_PORT={run_port}",
        "-e",
        f"CHAT_BROWSER_DRIVER_PORT={driver_port}",
        settings.CHAT_BROWSER_IMAGE,
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        detail = stderr.decode(errors="replace") or stdout.decode(errors="replace")
        raise RuntimeError(f"failed to start browser container: {detail}")
    container_id = stdout.decode().strip()
    if not container_id:
        raise RuntimeError("docker run returned empty container id")

    ws_endpoint_in_container = f"ws://{name}:{run_port}/"
    host_run_port = await _mapped_host_port(container_id, run_port)
    host_driver_port = await _mapped_host_port(container_id, driver_port)
    ws_endpoint_host = f"ws://127.0.0.1:{host_run_port}/"
    driver_endpoint_in_container = f"http://{name}:{driver_port}"
    driver_endpoint_host = f"http://127.0.0.1:{host_driver_port}"

    record = BrowserSessionRecord(
        container_id=container_id,
        ws_endpoint_in_container=ws_endpoint_in_container,
        ws_endpoint_host=ws_endpoint_host,
        driver_endpoint_in_container=driver_endpoint_in_container,
        driver_endpoint_host=driver_endpoint_host,
        idle_deadline=time.time() + settings.CHAT_BROWSER_IDLE_SEC,
    )
    await _save_record(conversation_id, record)
    try:
        await container_client.wait_until_healthy(record)
    except RuntimeError as exc:
        logs_proc = await asyncio.create_subprocess_exec(
            "docker",
            "logs",
            container_id,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        logs_out, _ = await logs_proc.communicate()
        tail = logs_out.decode(errors="replace")[-2000:]
        await _remove_container(name)
        await clear_record(conversation_id)
        raise RuntimeError(f"browser session driver failed health check: {exc}; logs_tail={tail!r}") from exc
    logger.info(
        "browser.session.started",
        conversation_id=conversation_id,
        container_id=container_id,
        host_run_port=host_run_port,
        host_driver_port=host_driver_port,
    )
    return record


async def ensure_session(conversation_id: int, workspace: Path) -> BrowserSessionRecord:
    record = await _load_record(conversation_id)
    if record is not None and await _container_running(record.container_id):
        record = BrowserSessionRecord(
            container_id=record.container_id,
            ws_endpoint_in_container=record.ws_endpoint_in_container,
            ws_endpoint_host=record.ws_endpoint_host,
            driver_endpoint_in_container=record.driver_endpoint_in_container,
            driver_endpoint_host=record.driver_endpoint_host,
            idle_deadline=time.time() + settings.CHAT_BROWSER_IDLE_SEC,
        )
        await _save_record(conversation_id, record)
        return record
    if record is not None:
        await clear_record(conversation_id)
    return await _start_container(conversation_id, workspace)


async def get_existing_session(conversation_id: int) -> BrowserSessionRecord | None:
    """Return a running session without creating a blank browser session."""
    record = await _load_record(conversation_id)
    if record is None:
        return None
    if not await _container_running(record.container_id):
        await clear_record(conversation_id)
        return None
    refreshed = BrowserSessionRecord(
        container_id=record.container_id,
        ws_endpoint_in_container=record.ws_endpoint_in_container,
        ws_endpoint_host=record.ws_endpoint_host,
        driver_endpoint_in_container=record.driver_endpoint_in_container,
        driver_endpoint_host=record.driver_endpoint_host,
        idle_deadline=time.time() + settings.CHAT_BROWSER_IDLE_SEC,
    )
    await _save_record(conversation_id, refreshed)
    return refreshed


async def invalidate_if_dead(conversation_id: int) -> None:
    record = await _load_record(conversation_id)
    if record is None:
        return
    if not await _container_running(record.container_id):
        await clear_record(conversation_id)
