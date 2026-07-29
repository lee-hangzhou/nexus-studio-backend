from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from app.agent.chat.browser import container_client, inprocess_session, session_manager, session_storage
from app.agent.chat.browser.session_manager import BrowserSessionRecord
from app.server.infra.config import settings


def _record() -> BrowserSessionRecord:
    return BrowserSessionRecord(
        container_id="child-id",
        ws_endpoint_in_container="ws://nexus-studio-browser-42:3333/",
        ws_endpoint_host="ws://127.0.0.1:49100/",
        driver_endpoint_in_container="http://nexus-studio-browser-42:3334",
        driver_endpoint_host="http://127.0.0.1:49101",
        idle_deadline=123.0,
    )


def test_child_ports_are_only_published_on_host_loopback():
    assert session_manager._loopback_publish_spec(3333) == "127.0.0.1:0:3333"
    assert session_manager._loopback_publish_spec(3334) == "127.0.0.1:0:3334"


def test_driver_endpoint_selects_network_in_container_and_mapping_on_host(monkeypatch):
    record = _record()

    monkeypatch.setattr(container_client, "_backend_runs_in_container", lambda: True)
    assert container_client._base_url(record) == "http://nexus-studio-browser-42:3334"

    monkeypatch.setattr(container_client, "_backend_runs_in_container", lambda: False)
    assert container_client._base_url(record) == "http://127.0.0.1:49101"


def test_legacy_loopback_record_is_upgraded_to_docker_network_endpoint(monkeypatch):
    monkeypatch.setattr(settings, "CHAT_BROWSER_RUN_SERVER_PORT", 3333)
    monkeypatch.setattr(settings, "CHAT_BROWSER_DRIVER_PORT", 3334)
    record = session_manager._record_from_dict(
        42,
        {
            "container_id": "child-id",
            "ws_endpoint_in_container": "ws://127.0.0.1:3333/",
            "ws_endpoint_host": "ws://127.0.0.1:49100/",
            "driver_endpoint_in_container": "http://127.0.0.1:3334",
            "driver_endpoint_host": "http://127.0.0.1:49101",
            "idle_deadline": 123.0,
        },
    )

    assert record is not None
    assert record.ws_endpoint_in_container == "ws://nexus-studio-browser-42:3333/"
    assert record.driver_endpoint_in_container == "http://nexus-studio-browser-42:3334"


@pytest.mark.asyncio
async def test_container_resolve_and_probe_never_use_inprocess(monkeypatch):
    record = _record()
    get_existing = AsyncMock(return_value=record)
    session_info = AsyncMock(return_value={"active": True, "url": "https://Example.COM/account"})
    probe = AsyncMock(return_value=True)
    forbidden = AsyncMock(side_effect=AssertionError("inprocess runtime called"))

    monkeypatch.setattr(settings, "CHAT_BROWSER_INPROCESS", False)
    monkeypatch.setattr(settings, "CHAT_LOGIN_PROBE_TIMEOUT_MS", 15000)
    monkeypatch.setattr(session_manager, "get_existing_session", get_existing)
    monkeypatch.setattr(container_client, "session_info", session_info)
    monkeypatch.setattr(container_client, "probe_logged_in", probe)
    monkeypatch.setattr(inprocess_session, "get_session_entry", forbidden)

    assert await session_storage.resolve_domain(42, None) == "example.com"
    assert await session_storage.probe_logged_in(42, "[data-user-menu]") is True
    probe.assert_awaited_once_with(record, selector="[data-user-menu]", timeout_ms=15000)
    forbidden.assert_not_awaited()


@pytest.mark.asyncio
async def test_container_save_and_restore_use_driver_storage_api(monkeypatch, tmp_path):
    record = _record()
    get_existing = AsyncMock(return_value=record)
    ensure_session = AsyncMock(return_value=record)
    save_storage = AsyncMock(return_value={"saved": True})
    restore_storage = AsyncMock(return_value={"restored": True})
    probe = AsyncMock(return_value=True)
    forbidden = AsyncMock(side_effect=AssertionError("inprocess runtime called"))

    monkeypatch.setattr(settings, "CHAT_BROWSER_INPROCESS", False)
    monkeypatch.setattr(settings, "CHAT_BROWSER_STORAGE_ENABLED", True)
    monkeypatch.setattr(session_manager, "get_existing_session", get_existing)
    monkeypatch.setattr(session_manager, "ensure_session", ensure_session)
    monkeypatch.setattr(container_client, "save_storage", save_storage)
    monkeypatch.setattr(container_client, "restore_storage", restore_storage)
    monkeypatch.setattr(container_client, "probe_logged_in", probe)
    monkeypatch.setattr(inprocess_session, "get_session_entry", forbidden)
    monkeypatch.setattr(inprocess_session, "recreate_with_storage", forbidden)
    monkeypatch.setattr(session_storage.site_auth, "get_site_auth", AsyncMock(return_value=None))

    saved = await session_storage.save_session(
        conversation_id=42,
        workspace=tmp_path,
        expected_domain="example.com",
    )
    assert saved.success
    save_storage.assert_awaited_once_with(
        record,
        path_rel=".browser/storage/example.com.json",
    )

    state_path = session_storage.storage_path(tmp_path, "example.com")
    state_path.write_text(json.dumps({"cookies": [], "origins": []}), encoding="utf-8")
    restored = await session_storage.restore_session(
        conversation_id=42,
        workspace=tmp_path,
        expected_domain="example.com",
        login_probe_selector="[data-user-menu]",
    )
    assert restored.success
    assert json.loads(restored.output)["logged_in"] is True
    restore_storage.assert_awaited_once_with(
        record,
        path_rel=".browser/storage/example.com.json",
    )
    forbidden.assert_not_awaited()
