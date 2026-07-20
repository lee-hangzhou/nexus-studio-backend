from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest


_DRIVER_PATH = Path(__file__).with_name("driver.py")
_SPEC = importlib.util.spec_from_file_location("browser_session_driver", _DRIVER_PATH)
assert _SPEC is not None and _SPEC.loader is not None
driver_module = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(driver_module)


class _FakePage:
    def __init__(self, url: str = "about:blank") -> None:
        self.url = url

    def is_closed(self) -> bool:
        return False


class _FakeContext:
    def __init__(self, page: _FakePage | None = None) -> None:
        self.page = page or _FakePage()
        self.saved_path: str | None = None
        self.closed = False

    async def storage_state(self, *, path: str) -> None:
        self.saved_path = path
        Path(path).write_text('{"cookies": [], "origins": []}', encoding="utf-8")

    async def new_page(self) -> _FakePage:
        return self.page

    async def close(self) -> None:
        self.closed = True


class _FakeBrowser:
    def __init__(self) -> None:
        self.storage_state: str | None = None
        self.context = _FakeContext(_FakePage())

    async def new_context(self, **kwargs: Any) -> _FakeContext:
        self.storage_state = kwargs.get("storage_state")
        return self.context


def test_storage_routes_are_registered():
    app = driver_module.build_app()
    routes = {(route.method, route.resource.canonical) for route in app.router.routes()}
    assert ("GET", "/v1/session") in routes
    assert ("POST", "/v1/session/probe") in routes
    assert ("POST", "/v1/storage/save") in routes
    assert ("POST", "/v1/storage/restore") in routes


def test_storage_path_cannot_escape_workspace(monkeypatch, tmp_path):
    monkeypatch.setattr(driver_module, "WORKSPACE_ROOT", tmp_path)
    assert driver_module._workspace_path(".browser/storage/example.com.json").is_relative_to(tmp_path)
    with pytest.raises(ValueError, match="escapes workspace"):
        driver_module._workspace_path("../outside.json")


@pytest.mark.asyncio
async def test_driver_saves_and_restores_context_storage(monkeypatch, tmp_path):
    monkeypatch.setattr(driver_module, "WORKSPACE_ROOT", tmp_path)
    session_driver = driver_module.BrowserSessionDriver()
    old_page = _FakePage("https://example.com/account")
    old_context = _FakeContext(old_page)
    fake_browser = _FakeBrowser()
    session_driver._page = old_page
    session_driver._context = old_context
    session_driver._browser = fake_browser

    path_rel = ".browser/storage/example.com.json"
    saved = await session_driver.save_storage(path_rel=path_rel)
    assert saved["saved"] is True
    assert old_context.saved_path == str(tmp_path / path_rel)

    restored = await session_driver.restore_storage(path_rel=path_rel)
    assert restored["restored"] is True
    assert fake_browser.storage_state == str(tmp_path / path_rel)
    assert old_context.closed is True
    assert session_driver._context is fake_browser.context
    assert session_driver._page is fake_browser.context.page
