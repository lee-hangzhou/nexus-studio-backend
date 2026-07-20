"""HTTP client for the long-lived browser session driver inside each container."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx

from app.chat.browser.exec_result import BrowserExecResult, parse_driver_exec_payload
from app.chat.browser.session_manager import BrowserSessionRecord
from app.core.config import settings


def _backend_runs_in_container() -> bool:
    return Path("/.dockerenv").exists() or Path("/run/.containerenv").exists()


def _base_url(record: BrowserSessionRecord) -> str:
    endpoint = (
        record.driver_endpoint_in_container
        if _backend_runs_in_container()
        else record.driver_endpoint_host
    )
    return endpoint.rstrip("/")


async def wait_until_healthy(record: BrowserSessionRecord, *, attempts: int = 60) -> None:
    url = f"{_base_url(record)}/health"
    last_error = "driver health check failed"
    async with httpx.AsyncClient(timeout=2.0) as client:
        for _ in range(attempts):
            try:
                resp = await client.get(url)
                if resp.status_code == 200 and resp.json().get("ok"):
                    return
                last_error = f"unexpected health response: {resp.status_code}"
            except Exception as exc:
                last_error = str(exc)
            await asyncio.sleep(0.5)
    raise RuntimeError(last_error)


async def exec_script(record: BrowserSessionRecord, code: str) -> BrowserExecResult:
    url = f"{_base_url(record)}/v1/exec"
    timeout = float(settings.CHAT_BROWSER_EXEC_TIMEOUT_SEC) + 10.0
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(url, json={"code": code})
        resp.raise_for_status()
        payload = resp.json()
        if not isinstance(payload, dict):
            raise RuntimeError("driver exec returned non-object")
        return parse_driver_exec_payload(payload)


async def capture_state(
    record: BrowserSessionRecord,
    *,
    image_rel: str,
    full_page: bool,
    text_max_chars: int,
    screenshot_url: str,
) -> dict[str, Any]:
    url = f"{_base_url(record)}/v1/capture"
    timeout = float(settings.CHAT_BROWSER_EXEC_TIMEOUT_SEC) + 10.0
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(
            url,
            json={
                "image_rel": image_rel,
                "full_page": full_page,
                "text_max_chars": text_max_chars,
                "screenshot_url": screenshot_url,
            },
        )
        resp.raise_for_status()
        payload = resp.json()
        data = payload.get("data")
        if not isinstance(data, dict):
            raise RuntimeError("driver capture returned invalid payload")
        return data


async def locator_screenshot(
    record: BrowserSessionRecord,
    *,
    path_rel: str,
    selector: str,
) -> None:
    url = f"{_base_url(record)}/v1/locator_screenshot"
    timeout = float(settings.CHAT_BROWSER_EXEC_TIMEOUT_SEC) + 10.0
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(url, json={"path_rel": path_rel, "selector": selector})
        resp.raise_for_status()


async def _post_driver_data(record: BrowserSessionRecord, path: str, payload: dict[str, Any]) -> dict[str, Any]:
    url = f"{_base_url(record)}{path}"
    timeout = float(settings.CHAT_BROWSER_EXEC_TIMEOUT_SEC) + 10.0
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(url, json=payload)
        resp.raise_for_status()
        body = resp.json()
        data = body.get("data")
        if not isinstance(data, dict):
            raise RuntimeError(f"driver {path} returned invalid payload")
        return data


async def session_info(record: BrowserSessionRecord) -> dict[str, Any]:
    url = f"{_base_url(record)}/v1/session"
    timeout = float(settings.CHAT_BROWSER_EXEC_TIMEOUT_SEC) + 10.0
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        body = resp.json()
        data = body.get("data")
        if not isinstance(data, dict):
            raise RuntimeError("driver session info returned invalid payload")
        return data


async def save_storage(record: BrowserSessionRecord, *, path_rel: str) -> dict[str, Any]:
    return await _post_driver_data(record, "/v1/storage/save", {"path_rel": path_rel})


async def restore_storage(record: BrowserSessionRecord, *, path_rel: str) -> dict[str, Any]:
    return await _post_driver_data(record, "/v1/storage/restore", {"path_rel": path_rel})


async def probe_logged_in(
    record: BrowserSessionRecord,
    *,
    selector: str,
    timeout_ms: int,
) -> bool:
    data = await _post_driver_data(
        record,
        "/v1/session/probe",
        {"selector": selector, "timeout_ms": timeout_ms},
    )
    return bool(data.get("matched"))


async def challenge_read_geometry(
    record: BrowserSessionRecord,
    *,
    container_selector: str,
    track_selector: str,
    handle_selector: str,
    scope_selector: str | None = None,
    content_selector: str | None = None,
) -> dict[str, Any]:
    return await _post_driver_data(
        record,
        "/v1/challenge/read_geometry",
        {
            "container_selector": container_selector,
            "track_selector": track_selector,
            "handle_selector": handle_selector,
            "scope_selector": scope_selector,
            "content_selector": content_selector,
        },
    )


async def challenge_screenshot_element(
    record: BrowserSessionRecord,
    *,
    selector: str,
    path_rel: str,
    scope_selector: str | None = None,
) -> dict[str, Any]:
    return await _post_driver_data(
        record,
        "/v1/challenge/screenshot_element",
        {
            "selector": selector,
            "path_rel": path_rel,
            "scope_selector": scope_selector,
        },
    )


async def challenge_dispatch_pointer_trace(
    record: BrowserSessionRecord,
    *,
    events: list[dict[str, Any]],
) -> None:
    url = f"{_base_url(record)}/v1/challenge/dispatch_pointer_trace"
    timeout = float(settings.CHAT_BROWSER_EXEC_TIMEOUT_SEC) + 10.0
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(url, json={"events": events})
        resp.raise_for_status()


async def challenge_wait_probe(
    record: BrowserSessionRecord,
    *,
    selector: str,
    success_selector: str | None = None,
    failure_selector: str | None = None,
    retry_text_probe: str | None = None,
    panel_selector: str | None = None,
    scope_selector: str | None = None,
    timeout_ms: int,
) -> dict[str, Any]:
    return await _post_driver_data(
        record,
        "/v1/challenge/wait_probe",
        {
            "selector": selector,
            "success_selector": success_selector or selector,
            "failure_selector": failure_selector,
            "retry_text_probe": retry_text_probe,
            "panel_selector": panel_selector,
            "scope_selector": scope_selector,
            "timeout_ms": timeout_ms,
        },
    )
