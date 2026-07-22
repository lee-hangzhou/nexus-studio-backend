"""Unified browser runtime: in-process (spike/tests) or container session driver (wave3)."""

from __future__ import annotations

import json
import textwrap
from contextlib import redirect_stdout, suppress
from dataclasses import dataclass
from io import StringIO
from pathlib import Path
from typing import Any

import httpx

from app.agent.chat.browser import container_client, inprocess_session, session_manager
from app.agent.chat.browser.exec_result import (
    BrowserExecExitCode,
    BrowserExecParseError,
    BrowserExecResult,
    exec_result_to_tool_result,
    parse_error_to_tool_result,
)
from app.agent.chat.browser.session_lock import session_page_lock
from app.agent.chat.gate.fields import field_def_name
from app.agent.chat.browser.script_validate import browser_script_uses_playwright_selector_in_evaluate
from app.agent.chat.tools.result import (
    BROWSER_ERROR,
    BROWSER_UNAVAILABLE,
    INVALID_ARGUMENTS,
    LOGIN_FAILED,
    MISSING_SELECTOR,
    TURN_INTERRUPTED,
    ToolResult,
)
from app.server.infra.config import settings


def use_inprocess_runtime() -> bool:
    return settings.CHAT_BROWSER_INPROCESS


@dataclass(frozen=True)
class GateActionParams:
    gate_type: str
    phase: str | None
    field_defs: list[dict[str, Any]]
    username_selector: str | None = None
    password_selector: str | None = None
    submit_selector: str | None = None
    phone_selector: str | None = None
    code_selector: str | None = None
    captcha_input_selector: str | None = None
    login_probe_selector: str | None = None
    login_probe_timeout_ms: int = 15_000


def _selector_for_field(name: str, params: GateActionParams) -> str | None:
    mapping = {
        "username": params.username_selector,
        "password": params.password_selector,
        "phone": params.phone_selector,
        "code": params.code_selector,
        "captcha": params.captcha_input_selector,
    }
    return mapping.get(name)


def _allowed_field_names(params: GateActionParams) -> set[str]:
    if params.gate_type == "phone_otp" and params.phase == "phone":
        return {"phone"}
    if params.gate_type == "phone_otp" and params.phase == "code":
        return {"code"}
    if params.gate_type == "image_captcha":
        return {"captcha"}
    return {field_def_name(item) for item in params.field_defs if field_def_name(item)}


def _validate_resume_selectors(
    *,
    params: GateActionParams,
    secrets: dict[str, Any],
) -> ToolResult | None:
    if params.gate_type == "qr_scan":
        return None

    allowed = _allowed_field_names(params)
    for name in allowed:
        if not name or name not in secrets:
            continue
        if not _selector_for_field(name, params):
            return ToolResult.fail(MISSING_SELECTOR, detail=f"missing selector for field: {name}")

    if params.gate_type == "credentials" and not params.submit_selector:
        return ToolResult.fail(MISSING_SELECTOR, detail="credentials requires submit_selector")
    if params.gate_type == "image_captcha" and not params.submit_selector:
        return ToolResult.fail(MISSING_SELECTOR, detail="image_captcha requires submit_selector")
    if params.gate_type == "phone_otp" and params.phase == "code" and not params.submit_selector:
        return ToolResult.fail(MISSING_SELECTOR, detail="phone_otp code phase requires submit_selector")

    return None


def _build_gate_fill_lines(
    *,
    params: GateActionParams,
    secrets: dict[str, Any],
) -> tuple[list[str], ToolResult | None]:
    fills: list[str] = []
    if params.gate_type == "qr_scan":
        return fills, None

    allowed = _allowed_field_names(params)
    for name in allowed:
        if not name or name not in secrets:
            continue
        selector = _selector_for_field(name, params)
        if not selector:
            return fills, ToolResult.fail(MISSING_SELECTOR, detail=f"missing selector for field: {name}")
        value = json.dumps(str(secrets[name]))
        selector_json = json.dumps(selector)
        fills.append(f"await page.fill({selector_json}, {value})")

    if params.gate_type == "credentials":
        if not params.submit_selector:
            return fills, ToolResult.fail(MISSING_SELECTOR, detail="credentials requires submit_selector")
        sel = json.dumps(params.submit_selector)
        fills.append(f"await page.locator({sel}).click()")
    elif params.gate_type == "image_captcha":
        if not params.submit_selector:
            return fills, ToolResult.fail(MISSING_SELECTOR, detail="image_captcha requires submit_selector")
        sel = json.dumps(params.submit_selector)
        fills.append(f"await page.locator({sel}).click()")
    elif params.gate_type == "phone_otp" and params.phase == "code":
        if not params.submit_selector:
            return fills, ToolResult.fail(MISSING_SELECTOR, detail="phone_otp code phase requires submit_selector")
        sel = json.dumps(params.submit_selector)
        fills.append(f"await page.locator({sel}).click()")

    if not fills:
        return fills, ToolResult.fail(MISSING_SELECTOR, detail="no gate fill actions to run")

    return fills, None


def _probe_line(params: GateActionParams) -> str:
    if not params.login_probe_selector:
        return ""
    return (
        f"await page.wait_for_selector({json.dumps(params.login_probe_selector)}, "
        f"timeout={params.login_probe_timeout_ms})"
    )


def _gate_submit_clicked(params: GateActionParams) -> bool:
    if params.gate_type in {"credentials", "image_captcha"}:
        return True
    return params.gate_type == "phone_otp" and params.phase == "code"


def _gate_action_result_payload(
    *,
    params: GateActionParams,
    url: str,
    fields_filled: list[str],
    probe_matched: bool | None = None,
) -> dict[str, Any]:
    """Factual gate write outcome — no logged-in conclusion."""
    payload: dict[str, Any] = {
        "status": "submit_dispatched" if _gate_submit_clicked(params) else "fields_dispatched",
        "gate_type": params.gate_type,
        "url": url,
    }
    if params.phase is not None:
        payload["phase"] = params.phase
    if fields_filled:
        payload["fields_filled"] = fields_filled
    if _gate_submit_clicked(params):
        payload["submit_clicked"] = True
    if params.login_probe_selector:
        payload["probe_selector"] = params.login_probe_selector
        payload["probe_matched"] = bool(probe_matched)
    return payload


def _gate_action_result_print_line(params: GateActionParams, fields_filled: list[str]) -> str:
    """Container exec: emit gate action JSON after fills/clicks (and optional probe)."""
    payload = _gate_action_result_payload(
        params=params,
        url="",
        fields_filled=fields_filled,
        probe_matched=True if params.login_probe_selector else None,
    )
    payload.pop("url", None)
    static = json.dumps(payload, ensure_ascii=False)
    return (
        "import json as _json\n"
        f"_payload = _json.loads({json.dumps(static)})\n"
        "_payload['url'] = page.url\n"
        "print(_json.dumps(_payload, ensure_ascii=False))"
    )


async def exec_user_script(
    *,
    conversation_id: int,
    workspace: Path,
    code: str,
    cancel_event: asyncio.Event | None = None,
) -> ToolResult:
    if cancel_event is not None and cancel_event.is_set():
        return ToolResult.fail(TURN_INTERRUPTED, detail="turn cancelled")
    body = textwrap.dedent(code).strip()
    if not body:
        return ToolResult.fail(BROWSER_ERROR, detail="empty code")
    if browser_script_uses_playwright_selector_in_evaluate(body):
        return ToolResult.fail(
            INVALID_ARGUMENTS,
            detail=(
                "page.evaluate runs browser DOM APIs only; Playwright selectors like "
                ":has-text() / :has() are invalid inside evaluate. "
                "Use page.locator(...).click() / page.click(...) for interactions, "
                "and standard CSS selectors in querySelector inside evaluate."
            ),
        )

    if use_inprocess_runtime():
        return await _exec_inprocess(conversation_id, workspace, body, cancel_event=cancel_event)

    if cancel_event is not None and cancel_event.is_set():
        return ToolResult.fail(TURN_INTERRUPTED, detail="turn cancelled")

    async with session_page_lock(conversation_id):
        record = await session_manager.ensure_session(conversation_id, workspace)
        try:
            exec_result = await container_client.exec_script(record, body)
        except BrowserExecParseError as exc:
            return parse_error_to_tool_result(exc)
        except FileNotFoundError:
            return ToolResult.fail(BROWSER_UNAVAILABLE, detail="docker not available on host")
        except httpx.TimeoutException:
            return ToolResult.fail(BROWSER_ERROR, detail="browser script timed out")
        except (httpx.HTTPError, RuntimeError) as exc:
            await session_manager.invalidate_if_dead(conversation_id)
            return ToolResult.fail(BROWSER_UNAVAILABLE, detail=str(exc))

    return exec_result_to_tool_result(exec_result)


async def _exec_inprocess(
    conversation_id: int,
    workspace: Path,
    body: str,
    *,
    cancel_event: asyncio.Event | None = None,
) -> ToolResult:
    import asyncio

    stdout_io = StringIO()
    stderr_io = StringIO()
    exit_code = BrowserExecExitCode.SUCCESS

    async with session_page_lock(conversation_id):
        if cancel_event is not None and cancel_event.is_set():
            return ToolResult.fail(TURN_INTERRUPTED, detail="turn cancelled")
        page = await inprocess_session.get_page(conversation_id)
        context = page.context
        workspace.mkdir(parents=True, exist_ok=True)
        raw_dir = workspace / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        local_vars: dict[str, Any] = {
            "page": page,
            "context": context,
            "Path": Path,
            "workspace": workspace,
            "raw_dir": raw_dir,
        }
        indented = textwrap.indent(body, "    ")
        wrapped = (
            "async def __user_exec__():\n"
            "    global page, context, Path, workspace, raw_dir\n"
            f"{indented}\n"
        )
        try:
            exec(wrapped, local_vars, local_vars)  # noqa: S102
            runner = local_vars.get("__user_exec__")
            if runner is None or not asyncio.iscoroutinefunction(runner):
                return ToolResult.fail(BROWSER_ERROR, detail="failed to compile user script")
            with redirect_stdout(stdout_io):
                runner_task = asyncio.create_task(runner())
                if cancel_event is None:
                    await runner_task
                else:
                    cancel_wait = asyncio.create_task(cancel_event.wait())
                    done, pending = await asyncio.wait(
                        {runner_task, cancel_wait},
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if cancel_wait in done:
                        runner_task.cancel()
                        with suppress(asyncio.CancelledError):
                            await runner_task
                        return ToolResult.fail(TURN_INTERRUPTED, detail="turn cancelled")
                    cancel_wait.cancel()
                    with suppress(asyncio.CancelledError):
                        await cancel_wait
                    await runner_task
        except Exception as exc:
            exit_code = 1
            stderr_io.write(str(exc))

    exec_result = BrowserExecResult(
        exit_code=int(exit_code),
        stdout=stdout_io.getvalue(),
        stderr=stderr_io.getvalue(),
    )
    return exec_result_to_tool_result(exec_result)


async def apply_gate_resume(
    *,
    conversation_id: int,
    workspace: Path,
    secrets: dict[str, Any],
    params: GateActionParams,
) -> ToolResult:
    validation = _validate_resume_selectors(params=params, secrets=secrets)
    if validation is not None:
        return validation

    if use_inprocess_runtime():
        return await _apply_gate_inprocess(
            conversation_id=conversation_id,
            secrets=secrets,
            params=params,
        )
    return await _apply_gate_container(
        conversation_id=conversation_id,
        workspace=workspace,
        secrets=secrets,
        params=params,
    )


async def _apply_gate_inprocess(
    *,
    conversation_id: int,
    secrets: dict[str, Any],
    params: GateActionParams,
) -> ToolResult:
    if params.gate_type == "qr_scan":
        return ToolResult.ok(json.dumps({"status": "qr_acknowledged"}))

    fields_filled: list[str] = []
    async with session_page_lock(conversation_id):
        page = await inprocess_session.get_page(conversation_id)
        try:
            allowed = _allowed_field_names(params)
            for name in allowed:
                if not name or name not in secrets:
                    continue
                selector = _selector_for_field(name, params)
                if not selector:
                    return ToolResult.fail(MISSING_SELECTOR, detail=f"missing selector for field: {name}")
                await page.fill(selector, str(secrets[name]))
                fields_filled.append(name)

            if params.gate_type == "credentials":
                if not params.submit_selector:
                    return ToolResult.fail(MISSING_SELECTOR, detail="credentials requires submit_selector")
                await page.locator(params.submit_selector).click()
            elif params.gate_type == "image_captcha":
                if not params.submit_selector:
                    return ToolResult.fail(MISSING_SELECTOR, detail="image_captcha requires submit_selector")
                await page.locator(params.submit_selector).click()
            elif params.gate_type == "phone_otp" and params.phase == "code":
                if not params.submit_selector:
                    return ToolResult.fail(MISSING_SELECTOR, detail="phone_otp code phase requires submit_selector")
                await page.locator(params.submit_selector).click()

            probe_matched: bool | None = None
            if params.login_probe_selector:
                await page.wait_for_selector(
                    params.login_probe_selector,
                    timeout=params.login_probe_timeout_ms,
                )
                probe_matched = True
        except Exception as exc:
            if params.login_probe_selector:
                return ToolResult.fail(LOGIN_FAILED, detail=str(exc))
            return ToolResult.fail(BROWSER_ERROR, detail=str(exc))
        url = page.url
    payload = _gate_action_result_payload(
        params=params,
        url=url,
        fields_filled=fields_filled,
        probe_matched=probe_matched,
    )
    return ToolResult.ok(json.dumps(payload, ensure_ascii=False))


async def _apply_gate_container(
    *,
    conversation_id: int,
    workspace: Path,
    secrets: dict[str, Any],
    params: GateActionParams,
) -> ToolResult:
    if params.gate_type == "qr_scan":
        return ToolResult.ok(json.dumps({"status": "qr_acknowledged"}))

    fills, fill_err = _build_gate_fill_lines(params=params, secrets=secrets)
    if fill_err is not None:
        return fill_err

    probe = _probe_line(params)
    body_lines = fills + ([probe] if probe else [])
    filled_names = [
        name
        for name in _allowed_field_names(params)
        if name and name in secrets
    ]
    body_lines.append(_gate_action_result_print_line(params, filled_names))
    return await exec_user_script(
        conversation_id=conversation_id,
        workspace=workspace,
        code="\n".join(body_lines),
    )


async def locator_bounding_box(
    conversation_id: int,
    selector: str,
    *,
    workspace: Path,
) -> dict[str, float] | None:
    if use_inprocess_runtime():
        from app.agent.chat.browser import inprocess_session

        async with session_page_lock(conversation_id):
            page = await inprocess_session.get_page(conversation_id)
            return await page.locator(selector).bounding_box()

    code = textwrap.dedent(
        f"""
        bbox = await page.locator({json.dumps(selector)}).bounding_box()
        print(json.dumps(bbox))
        """
    ).strip()
    result = await exec_user_script(
        conversation_id=conversation_id,
        workspace=workspace,
        code=code,
    )
    if not result.success:
        return None
    try:
        parsed = json.loads(result.output.strip() or "null")
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


async def click_selector(
    *,
    conversation_id: int,
    workspace: Path,
    selector: str,
    scope_selector: str | None = None,
) -> ToolResult:
    if not selector.strip():
        return ToolResult.fail(MISSING_SELECTOR, detail="empty selector")
    if use_inprocess_runtime():
        async with session_page_lock(conversation_id):
            page = await inprocess_session.get_page(conversation_id)
            try:
                loc = (
                    page.locator(scope_selector).locator(selector)
                    if scope_selector
                    else page.locator(selector)
                )
                await loc.click(timeout=15000)
            except Exception as exc:
                return ToolResult.fail(BROWSER_ERROR, detail=str(exc))
        return ToolResult.ok('{"status":"clicked"}')
    scope_line = (
        f"scope = page.locator({json.dumps(scope_selector)})\n"
        f"await scope.locator({json.dumps(selector)}).click(timeout=15000)\n"
        if scope_selector
        else f"await page.locator({json.dumps(selector)}).click(timeout=15000)\n"
    )
    code = textwrap.dedent(
        f"""
        {scope_line}
        print(json.dumps({{"status": "clicked"}}))
        """
    ).strip()
    return await exec_user_script(
        conversation_id=conversation_id,
        workspace=workspace,
        code=code,
    )
