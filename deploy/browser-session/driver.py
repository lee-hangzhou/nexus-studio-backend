"""Long-lived browser session driver — one Playwright attachment per container.

All browser_exec_script / capture / gate operations go through this
process so exec and capture always share the same Page object (same model as
inprocess_session on the host).
"""

from __future__ import annotations

import asyncio
import json
import os
import textwrap
import traceback
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from typing import Any

from aiohttp import web
from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright

WORKSPACE_ROOT = Path(os.environ.get("CHAT_BROWSER_WORKSPACE_ROOT", "/workspace"))
RUN_SERVER_WS = os.environ.get(
    "CHAT_BROWSER_RUN_SERVER_WS",
    f"ws://127.0.0.1:{os.environ.get('CHAT_BROWSER_RUN_SERVER_PORT', '3333')}/",
)
DRIVER_PORT = int(os.environ.get("CHAT_BROWSER_DRIVER_PORT", "3334"))
EXEC_TIMEOUT_SEC = int(os.environ.get("CHAT_BROWSER_EXEC_TIMEOUT_SEC", "180"))


def _workspace_path(path_rel: str) -> Path:
    relative = Path(path_rel)
    if not path_rel.strip() or relative.is_absolute():
        raise ValueError("path_rel must be a non-empty relative path")
    root = WORKSPACE_ROOT.resolve()
    target = (root / relative).resolve()
    if not target.is_relative_to(root):
        raise ValueError("path_rel escapes workspace")
    return target


class BrowserSessionDriver:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None

    async def _ensure_browser(self) -> None:
        if self._page is not None:
            return
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.connect(RUN_SERVER_WS)
        if self._browser.contexts:
            self._context = self._browser.contexts[0]
        else:
            self._context = await self._browser.new_context()
        pages = self._context.pages
        if pages:
            self._page = await self._pick_operational_page(pages)
        else:
            self._page = await self._context.new_page()
        await self._prune_blank_tabs()

    async def _pick_operational_page(self, pages: list[Page]) -> Page:
        for page in reversed(pages):
            url = page.url or ""
            if url and url != "about:blank":
                return page
        return pages[-1]

    async def _prune_blank_tabs(self) -> None:
        if self._context is None or self._page is None:
            return
        for extra in list(self._context.pages):
            if extra is self._page:
                continue
            url = extra.url or ""
            if url in ("", "about:blank"):
                try:
                    await extra.close()
                except Exception:
                    pass

    async def _sync_active_page_after_exec_async(self, page_before: Page, page_after: Any) -> None:
        if self._context is None:
            return
        candidate = page_after if isinstance(page_after, Page) else page_before
        if candidate in self._context.pages:
            self._page = candidate
            return
        pages = self._context.pages
        if pages:
            self._page = await self._pick_operational_page(pages)

    async def exec_code(self, code: str) -> dict[str, Any]:
        body = textwrap.dedent(code).strip()
        if not body:
            return {"rc": 1, "stdout": "", "stderr": "empty code"}

        async with self._lock:
            await self._ensure_browser()
            assert self._page is not None
            assert self._context is not None

            page_before = self._page
            workspace = WORKSPACE_ROOT
            raw_dir = workspace / "raw"
            raw_dir.mkdir(parents=True, exist_ok=True)
            local_vars: dict[str, Any] = {
                "page": self._page,
                "context": self._context,
                "Path": Path,
                "workspace": workspace,
                "raw_dir": raw_dir,
                "json": json,
            }
            indented = textwrap.indent(body, "    ")
            wrapped = (
                "async def __user_exec__():\n"
                "    global page, context, Path, workspace, raw_dir\n"
                f"{indented}\n"
            )
            stdout_io = StringIO()
            stderr_io = StringIO()
            rc = 0
            try:
                exec(wrapped, local_vars, local_vars)  # noqa: S102
                runner = local_vars.get("__user_exec__")
                if runner is None or not asyncio.iscoroutinefunction(runner):
                    return {"rc": 1, "stdout": "", "stderr": "failed to compile user script"}
                with redirect_stdout(stdout_io), redirect_stderr(stderr_io):
                    await asyncio.wait_for(runner(), timeout=EXEC_TIMEOUT_SEC)
            except asyncio.TimeoutError:
                rc = 1
                stderr_io.write("browser script timed out")
            except Exception:
                rc = 1
                stderr_io.write(traceback.format_exc())

            await self._sync_active_page_after_exec_async(page_before, local_vars.get("page"))
            await self._prune_blank_tabs()
            return {
                "rc": rc,
                "stdout": stdout_io.getvalue(),
                "stderr": stderr_io.getvalue(),
            }

    async def capture_state(
        self,
        *,
        image_rel: str,
        full_page: bool,
        text_max_chars: int,
        screenshot_url: str,
    ) -> dict[str, Any]:
        code = f"""
image_path = workspace / {json.dumps(image_rel)}
image_path.parent.mkdir(parents=True, exist_ok=True)
text = await page.evaluate(\"\"\"(limit) => (document.body?.innerText || "").slice(0, limit)\"\"\", {text_max_chars})
await page.screenshot(path=str(image_path), full_page={repr(full_page)}, type="png")
data = {{
    "url": page.url,
    "title": await page.title(),
    "text_preview": text or "",
    "screenshot_path": {image_rel!r},
    "screenshot_url": {screenshot_url!r},
}}
print(json.dumps(data, ensure_ascii=False))
"""
        result = await self.exec_code(code)
        if result["rc"] != 0:
            raise RuntimeError((result.get("stderr") or result.get("stdout") or "capture failed").strip())
        stdout = str(result.get("stdout") or "").strip()
        if not stdout:
            raise RuntimeError("capture returned empty stdout")
        return json.loads(stdout.splitlines()[-1])

    async def locator_screenshot(self, *, path_rel: str, selector: str) -> None:
        code = (
            f"out = workspace / {path_rel!r}\n"
            f"out.parent.mkdir(parents=True, exist_ok=True)\n"
            f"loc = page.locator({selector!r})\n"
            f"await loc.screenshot(path=str(out), type='png')\n"
        )
        result = await self.exec_code(code)
        if result["rc"] != 0:
            raise RuntimeError((result.get("stderr") or result.get("stdout") or "locator screenshot failed").strip())

    async def challenge_read_geometry(
        self,
        *,
        container_selector: str,
        track_selector: str,
        handle_selector: str,
        scope_selector: str | None = None,
        content_selector: str | None = None,
    ) -> dict[str, Any]:
        code = f"""
import json

async def _resolve_visible_bbox(sel, scope=None):
    root = page.locator(scope) if scope else page
    loc = root.locator(sel) if scope else page.locator(sel)
    count = await loc.count()
    for i in range(count):
        cand = loc.nth(i)
        try:
            if not await cand.is_visible():
                continue
            box = await cand.bounding_box()
            if not box or float(box.get("width") or 0) < 1 or float(box.get("height") or 0) < 1:
                continue
            return box, i, count
        except Exception:
            continue
    raise RuntimeError(f"challenge element not found: {{sel}}")

scope = {scope_selector!r} or None
c_sel = {container_selector!r}
t_sel = {track_selector!r}
h_sel = {handle_selector!r}
container, c_idx, c_count = await _resolve_visible_bbox(c_sel, scope)
track, t_idx, t_count = await _resolve_visible_bbox(t_sel, scope)
handle, h_idx, h_count = await _resolve_visible_bbox(h_sel, scope)
viewport = page.viewport_size or {{"width": 1280, "height": 800}}
instruction = ""
try:
    root = page.locator(scope).locator(c_sel).nth(c_idx) if scope else page.locator(c_sel).nth(c_idx)
    instruction = (await root.inner_text())[:500]
except Exception:
    pass
content_sel = {content_selector!r} or t_sel
content_width_px = None
try:
    cloc = page.locator(scope).locator(content_sel).first if scope else page.locator(content_sel).first
    val = await cloc.evaluate("el => el.naturalWidth || (el.tagName === 'CANVAS' ? el.width : null)")
    if val:
        content_width_px = float(val)
except Exception:
    pass
print(json.dumps({{
    "container": container,
    "track": track,
    "handle": handle,
    "viewport": viewport,
    "frame": {{"x": 0, "y": 0, "width": viewport["width"], "height": viewport["height"]}},
    "instruction_text": instruction,
    "resolved_index": {{"container": c_idx, "track": t_idx, "handle": h_idx}},
    "candidate_count": {{"container": c_count, "track": t_count, "handle": h_count}},
    "content_width_px": content_width_px,
}}))
"""
        result = await self.exec_code(code)
        if result["rc"] != 0:
            raise RuntimeError((result.get("stderr") or result.get("stdout") or "challenge read geometry failed").strip())
        stdout = str(result.get("stdout") or "").strip()
        if not stdout:
            raise RuntimeError("challenge read geometry returned empty stdout")
        return json.loads(stdout.splitlines()[-1])

    async def challenge_screenshot_element(
        self, *, selector: str, path_rel: str, scope_selector: str | None = None
    ) -> dict[str, Any]:
        code = f"""
import json

scope = {scope_selector!r} or None
sel = {selector!r}
path_rel = {path_rel!r}
root = page.locator(scope) if scope else page
loc = root.locator(sel) if scope else page.locator(sel)
count = await loc.count()
target = None
for i in range(count):
    cand = loc.nth(i)
    try:
        if await cand.is_visible():
            target = cand
            break
    except Exception:
        continue
if target is None:
    raise RuntimeError(f"challenge element not found: {{sel}}")
bbox = await target.bounding_box()
if not bbox:
    raise RuntimeError(f"challenge element not found: {{sel}}")
out = workspace / path_rel
out.parent.mkdir(parents=True, exist_ok=True)
await target.screenshot(path=str(out), type="png")
print(json.dumps({{"bbox_page": bbox}}))
"""
        result = await self.exec_code(code)
        if result["rc"] != 0:
            raise RuntimeError((result.get("stderr") or result.get("stdout") or "challenge screenshot failed").strip())
        stdout = str(result.get("stdout") or "").strip()
        if not stdout:
            raise RuntimeError("challenge screenshot returned empty stdout")
        return json.loads(stdout.splitlines()[-1])

    async def challenge_dispatch_pointer_trace(self, *, events: list[dict[str, Any]]) -> None:
        events_json = json.dumps(events, ensure_ascii=False)
        code = f"""
import json

events = json.loads({events_json!r})
prev_t = 0
for ev in events:
    delay = int(ev.get("t") or 0) - prev_t
    if delay > 0:
        await page.wait_for_timeout(delay)
    prev_t = int(ev.get("t") or 0)
    x = float(ev.get("x") or 0)
    y = float(ev.get("y") or 0)
    ev_type = str(ev.get("type") or "")
    if ev_type == "down":
        await page.mouse.move(x, y)
        await page.mouse.down()
    elif ev_type == "move":
        await page.mouse.move(x, y)
    elif ev_type == "up":
        await page.mouse.move(x, y)
        await page.mouse.up()
"""
        result = await self.exec_code(code)
        if result["rc"] != 0:
            raise RuntimeError((result.get("stderr") or result.get("stdout") or "challenge dispatch failed").strip())

    async def challenge_wait_probe(
        self,
        *,
        selector: str,
        success_selector: str | None = None,
        failure_selector: str | None = None,
        retry_text_probe: str | None = None,
        panel_selector: str | None = None,
        scope_selector: str | None = None,
        timeout_ms: int,
    ) -> dict[str, Any]:
        code = f"""
import json
import time

scope = {scope_selector!r} or None
success_sel = {success_selector!r} or {selector!r}
failure_sel = {failure_selector!r} or None
panel_sel = {panel_selector!r} or None
retry_text = {retry_text_probe!r} or None
timeout = {int(timeout_ms)}
poll_ms = 200

def _loc(sel):
    if not sel:
        return None
    root = page.locator(scope) if scope else page
    return root.locator(sel) if scope else page.locator(sel)

async def _visible(sel):
    loc = _loc(sel)
    if loc is None:
        return False
    try:
        if await loc.count() == 0:
            return False
        return await loc.first.is_visible()
    except Exception:
        return False

async def _panel_dismissed():
    if not panel_sel:
        return False
    loc = _loc(panel_sel)
    try:
        if loc is None or await loc.count() == 0:
            return True
        box = await loc.first.bounding_box()
        if box is None:
            return True
        return float(box.get("width") or 0) <= 0 or float(box.get("height") or 0) <= 0
    except Exception:
        return True

url_before = page.url
deadline = time.monotonic() + timeout / 1000.0
obs = {{"success_visible": False, "failure_visible": False, "retry_text_visible": False, "panel_dismissed": False}}
outcome = "inconclusive"
verified = False
error = "probe timeout"

while time.monotonic() < deadline:
    obs["success_visible"] = await _visible(success_sel)
    obs["failure_visible"] = await _visible(failure_sel)
    obs["retry_text_visible"] = False
    if retry_text:
        try:
            obs["retry_text_visible"] = await page.get_by_text(retry_text, exact=False).first.is_visible()
        except Exception:
            obs["retry_text_visible"] = False
    obs["panel_dismissed"] = await _panel_dismissed()
    if obs["success_visible"]:
        outcome = "passed"
        verified = True
        error = None
        break
    if obs["failure_visible"] or obs["retry_text_visible"]:
        outcome = "failed"
        error = "failure signal observed"
        break
    if panel_sel and obs["panel_dismissed"]:
        outcome = "dismissed"
        error = "challenge panel dismissed"
        break
    await page.wait_for_timeout(poll_ms)

url_after = page.url
print(json.dumps({{
    "outcome": outcome,
    "verified": verified,
    "visible": obs["success_visible"],
    "observations": obs,
    "url_before": url_before,
    "url_after": url_after,
    "selector": success_sel,
    "error": error,
}}))
"""
        result = await self.exec_code(code)
        if result["rc"] != 0:
            raise RuntimeError((result.get("stderr") or result.get("stdout") or "challenge wait probe failed").strip())
        stdout = str(result.get("stdout") or "").strip()
        if not stdout:
            raise RuntimeError("challenge wait probe returned empty stdout")
        return json.loads(stdout.splitlines()[-1])

    async def session_info(self) -> dict[str, Any]:
        async with self._lock:
            active = self._page is not None and not self._page.is_closed()
            return {
                "active": active,
                "url": self._page.url if active and self._page is not None else None,
            }

    async def save_storage(self, *, path_rel: str) -> dict[str, Any]:
        target = _workspace_path(path_rel)
        async with self._lock:
            if self._page is None or self._page.is_closed() or self._context is None:
                raise RuntimeError("no active browser session to save")
            target.parent.mkdir(parents=True, exist_ok=True)
            await self._context.storage_state(path=str(target))
            return {"saved": True, "path_rel": path_rel, "url": self._page.url}

    async def restore_storage(self, *, path_rel: str) -> dict[str, Any]:
        target = _workspace_path(path_rel)
        if not target.is_file():
            raise FileNotFoundError(f"storage state not found: {path_rel}")
        async with self._lock:
            await self._ensure_browser()
            assert self._browser is not None
            old_context = self._context
            new_context = await self._browser.new_context(storage_state=str(target))
            new_page = await new_context.new_page()
            self._context = new_context
            self._page = new_page
            if old_context is not None:
                try:
                    await old_context.close()
                except Exception:
                    pass
            return {"restored": True, "path_rel": path_rel, "url": new_page.url}

    async def probe_logged_in(self, *, selector: str, timeout_ms: int) -> dict[str, Any]:
        async with self._lock:
            if self._page is None or self._page.is_closed():
                return {"matched": False}
            try:
                locator = self._page.locator(selector)
                await locator.wait_for(state="visible", timeout=timeout_ms)
                return {"matched": True}
            except Exception:
                return {"matched": False}

    async def health(self) -> dict[str, Any]:
        async with self._lock:
            if self._page is None:
                return {"ok": True, "page_url": None, "title": None}
            return {
                "ok": True,
                "page_url": self._page.url,
                "title": await self._page.title(),
            }


driver = BrowserSessionDriver()


async def handle_health(_request: web.Request) -> web.Response:
    payload = await driver.health()
    return web.json_response(payload)


async def handle_exec(request: web.Request) -> web.Response:
    body = await request.json()
    code = str(body.get("code") or "")
    result = await driver.exec_code(code)
    return web.json_response(result)


async def handle_session_info(_request: web.Request) -> web.Response:
    data = await driver.session_info()
    return web.json_response({"data": data})


async def handle_storage_save(request: web.Request) -> web.Response:
    body = await request.json()
    data = await driver.save_storage(path_rel=str(body.get("path_rel") or ""))
    return web.json_response({"data": data})


async def handle_storage_restore(request: web.Request) -> web.Response:
    body = await request.json()
    data = await driver.restore_storage(path_rel=str(body.get("path_rel") or ""))
    return web.json_response({"data": data})


async def handle_session_probe(request: web.Request) -> web.Response:
    body = await request.json()
    selector = str(body.get("selector") or "")
    if not selector:
        return web.json_response({"error": "selector required"}, status=400)
    data = await driver.probe_logged_in(
        selector=selector,
        timeout_ms=int(body.get("timeout_ms") or 15000),
    )
    return web.json_response({"data": data})


async def handle_capture(request: web.Request) -> web.Response:
    body = await request.json()
    data = await driver.capture_state(
        image_rel=str(body.get("image_rel") or ""),
        full_page=bool(body.get("full_page", True)),
        text_max_chars=int(body.get("text_max_chars") or 1200),
        screenshot_url=str(body.get("screenshot_url") or ""),
    )
    return web.json_response({"data": data})


async def handle_locator_screenshot(request: web.Request) -> web.Response:
    body = await request.json()
    await driver.locator_screenshot(
        path_rel=str(body.get("path_rel") or ""),
        selector=str(body.get("selector") or ""),
    )
    return web.json_response({"ok": True})


async def handle_challenge_read_geometry(request: web.Request) -> web.Response:
    body = await request.json()
    data = await driver.challenge_read_geometry(
        container_selector=str(body.get("container_selector") or ""),
        track_selector=str(body.get("track_selector") or ""),
        handle_selector=str(body.get("handle_selector") or ""),
        scope_selector=str(body.get("scope_selector") or "") or None,
        content_selector=str(body.get("content_selector") or "") or None,
    )
    return web.json_response({"data": data})


async def handle_challenge_screenshot_element(request: web.Request) -> web.Response:
    body = await request.json()
    data = await driver.challenge_screenshot_element(
        selector=str(body.get("selector") or ""),
        path_rel=str(body.get("path_rel") or ""),
        scope_selector=str(body.get("scope_selector") or "") or None,
    )
    return web.json_response({"data": data})


async def handle_challenge_dispatch_pointer_trace(request: web.Request) -> web.Response:
    body = await request.json()
    events = body.get("events")
    if not isinstance(events, list):
        return web.json_response({"error": "events required"}, status=400)
    await driver.challenge_dispatch_pointer_trace(events=events)
    return web.json_response({"ok": True})


async def handle_challenge_wait_probe(request: web.Request) -> web.Response:
    body = await request.json()
    data = await driver.challenge_wait_probe(
        selector=str(body.get("selector") or body.get("success_selector") or ""),
        success_selector=str(body.get("success_selector") or body.get("selector") or "") or None,
        failure_selector=str(body.get("failure_selector") or "") or None,
        retry_text_probe=str(body.get("retry_text_probe") or "") or None,
        panel_selector=str(body.get("panel_selector") or "") or None,
        scope_selector=str(body.get("scope_selector") or "") or None,
        timeout_ms=int(body.get("timeout_ms") or 5000),
    )
    return web.json_response({"data": data})


def build_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/health", handle_health)
    app.router.add_get("/v1/session", handle_session_info)
    app.router.add_post("/v1/exec", handle_exec)
    app.router.add_post("/v1/storage/save", handle_storage_save)
    app.router.add_post("/v1/storage/restore", handle_storage_restore)
    app.router.add_post("/v1/session/probe", handle_session_probe)
    app.router.add_post("/v1/capture", handle_capture)
    app.router.add_post("/v1/locator_screenshot", handle_locator_screenshot)
    app.router.add_post("/v1/challenge/read_geometry", handle_challenge_read_geometry)
    app.router.add_post("/v1/challenge/screenshot_element", handle_challenge_screenshot_element)
    app.router.add_post("/v1/challenge/dispatch_pointer_trace", handle_challenge_dispatch_pointer_trace)
    app.router.add_post("/v1/challenge/wait_probe", handle_challenge_wait_probe)
    return app


def main() -> None:
    web.run_app(build_app(), host="0.0.0.0", port=DRIVER_PORT, print=None)


if __name__ == "__main__":
    main()
