"""UserGate tool: interrupt/resume for typed login/verification gates."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Annotated, Any, Literal

from langchain.tools import InjectedToolArg
from langchain_core.runnables import RunnableConfig
from langgraph.prebuilt import InjectedState
from langgraph.types import interrupt

from app.agent.chat.browser import runtime as browser_runtime
from app.agent.chat.browser.runtime import GateActionParams
from app.agent.chat.browser import session_storage
from app.agent.chat.gate import assets as gate_assets
from app.agent.chat.gate import meta as gate_meta
from app.agent.chat.gate import otp_flow
from app.agent.chat.gate import site_auth
from app.agent.chat.gate import turn_auth
from app.agent.chat.gate import vault
from app.agent.chat.gate.fields import field_names, normalize_field_defs
from app.agent.chat.gate.public_payload import strip_public_gate_payload
from app.agent.chat.gate.qr_verify import GateCaptureError
from app.agent.chat.tools.result import (
    BROWSER_ERROR,
    GATE_CANCELLED,
    INVALID_ARGUMENTS,
    LOGIN_METHOD_REQUIRED,
    MISSING_SELECTOR,
    ToolResult,
)
from app.server.infra.config import settings

GateType = Literal[
    "login_method",
    "credentials",
    "phone_otp",
    "qr_scan",
    "image_captcha",
    "confirm",
    "session_bridge",
]
PhoneOtpPhase = Literal["phone", "code"]
AUTH_TARGET_GATES: frozenset[str] = frozenset({"credentials", "phone_otp", "qr_scan", "image_captcha"})

GATE_NODE_ENTRY_COUNT = 0


def reset_gate_counters() -> None:
    global GATE_NODE_ENTRY_COUNT
    GATE_NODE_ENTRY_COUNT = 0


def _conversation_id_from_config(config: RunnableConfig | None) -> int:
    if not config:
        raise ValueError("missing runnable config")
    raw = (config.get("configurable") or {}).get("conversation_id")
    if raw is None:
        raise ValueError("missing conversation_id in config")
    return int(raw)


def _turn_id_from_config(config: RunnableConfig | None) -> str:
    if not config:
        return ""
    return str((config.get("configurable") or {}).get("turn_id") or "")


def _normalize_choices(raw: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    if not raw:
        return []
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        choice_id = str(item.get("id") or item.get("value") or "").strip()
        label = str(item.get("label") or "").strip()
        target_gate = str(item.get("target_gate") or "").strip()
        if not choice_id or not label or target_gate not in AUTH_TARGET_GATES:
            continue
        if choice_id in seen:
            continue
        seen.add(choice_id)
        entry: dict[str, Any] = {
            "id": choice_id,
            "label": label,
            "target_gate": target_gate,
        }
        tab_selector = str(item.get("tab_selector") or "").strip()
        if tab_selector:
            entry["tab_selector"] = tab_selector
        out.append(entry)
    return out


def _validate_gate_before_interrupt(
    *,
    gate_type: GateType,
    phase: PhoneOtpPhase | None,
    field_defs: list[dict[str, Any]],
    username_selector: str | None,
    password_selector: str | None,
    submit_selector: str | None,
    phone_selector: str | None,
    code_selector: str | None,
    captcha_image_selector: str | None,
    captcha_input_selector: str | None,
    qr_image_selector: str | None,
    expected_domain: str | None,
    choices: list[dict[str, Any]] | None,
    method_selected: bool,
) -> ToolResult | None:
    if gate_type == "login_method":
        normalized = _normalize_choices(choices)
        if not normalized:
            return ToolResult.fail(INVALID_ARGUMENTS, detail="login_method requires choices from inspect")
        return None

    if gate_type in AUTH_TARGET_GATES and not method_selected:
        return ToolResult.fail(
            LOGIN_METHOD_REQUIRED,
            detail="complete login_method gate before opening target auth gate",
        )

    if gate_type == "confirm":
        if field_defs:
            return ToolResult.fail(INVALID_ARGUMENTS, detail="confirm does not collect fields")
        return None

    if gate_type == "session_bridge":
        if field_defs:
            return ToolResult.fail(INVALID_ARGUMENTS, detail="session_bridge does not collect fields")
        if not expected_domain:
            return ToolResult.fail(INVALID_ARGUMENTS, detail="session_bridge requires expected_domain")
        return None

    if gate_type == "phone_otp" and phase is None:
        return ToolResult.fail(INVALID_ARGUMENTS, detail="phone_otp requires phase")

    if gate_type == "credentials":
        if not field_defs:
            return ToolResult.fail(INVALID_ARGUMENTS, detail="credentials requires fields")
        names = field_names(field_defs)
        if "username" in names and not username_selector:
            return ToolResult.fail(MISSING_SELECTOR, detail="credentials requires username_selector")
        if "password" in names and not password_selector:
            return ToolResult.fail(MISSING_SELECTOR, detail="credentials requires password_selector")
        if not submit_selector:
            return ToolResult.fail(MISSING_SELECTOR, detail="credentials requires submit_selector")
        if "captcha" in names:
            return ToolResult.fail(
                INVALID_ARGUMENTS,
                detail="use image_captcha gate for captcha, not credentials",
            )
        return None

    if gate_type == "image_captcha":
        if not captcha_image_selector or not captcha_input_selector:
            return ToolResult.fail(
                MISSING_SELECTOR,
                detail="image_captcha requires captcha_image_selector and captcha_input_selector",
            )
        if not submit_selector:
            return ToolResult.fail(MISSING_SELECTOR, detail="image_captcha requires submit_selector")
        if not field_defs:
            return ToolResult.fail(INVALID_ARGUMENTS, detail="image_captcha requires captcha field")
        return None

    if gate_type == "phone_otp":
        if phase == "phone":
            if not phone_selector:
                return ToolResult.fail(MISSING_SELECTOR, detail="phone_otp phone phase requires phone_selector")
            return None
        if phase == "code":
            if not code_selector:
                return ToolResult.fail(MISSING_SELECTOR, detail="phone_otp code phase requires code_selector")
            if not submit_selector:
                return ToolResult.fail(MISSING_SELECTOR, detail="phone_otp code phase requires submit_selector")
            return None
        return ToolResult.fail(INVALID_ARGUMENTS, detail="phone_otp requires phase phone or code")

    if gate_type == "qr_scan":
        if not qr_image_selector:
            return ToolResult.fail(MISSING_SELECTOR, detail="qr_scan requires qr_image_selector")
        return None

    return ToolResult.fail(INVALID_ARGUMENTS, detail=f"unknown gate_type: {gate_type}")


async def _apply_session_bridge_cookies(
    *,
    conversation_id: int,
    workspace,
    gate_id: str,
) -> ToolResult:
    secrets = await vault.take(gate_id)
    if not secrets or "_bridge_cookies" not in secrets:
        return ToolResult.fail(INVALID_ARGUMENTS, detail="bridge cookies not imported")
    cookies = json.loads(str(secrets["_bridge_cookies"]))
    code = f"await context.add_cookies({json.dumps(cookies)})\nprint(json.dumps({{'status': 'cookies_applied'}}))"
    return await browser_runtime.exec_user_script(
        conversation_id=conversation_id,
        workspace=workspace,
        code=code,
    )


async def _release_gate(workspace: Path, gate_id: str) -> None:
    gate_assets.delete_gate_assets(workspace, gate_id)
    await gate_meta.clear_gate_meta(gate_id)


def _choice_by_id(choices: list[dict[str, Any]], choice_id: str) -> dict[str, Any] | None:
    for item in choices:
        if str(item.get("id") or "") == choice_id:
            return item
    return None


async def _resolve_gate_domain(
    *,
    conversation_id: int,
    expected_domain: str | None,
) -> str | None:
    if expected_domain:
        try:
            return session_storage.normalize_domain(expected_domain)
        except ValueError:
            return None
    try:
        return await session_storage.resolve_domain(conversation_id, None)
    except ValueError:
        return None


async def request_user_gate(
    gate_type: GateType,
    prompt: str,
    fields: list[dict[str, Any]] | None = None,
    phase: PhoneOtpPhase | None = None,
    choices: list[dict[str, Any]] | None = None,
    username_selector: str | None = None,
    password_selector: str | None = None,
    submit_selector: str | None = None,
    phone_selector: str | None = None,
    code_selector: str | None = None,
    captcha_image_selector: str | None = None,
    captcha_input_selector: str | None = None,
    qr_image_selector: str | None = None,
    login_probe_selector: str | None = None,
    login_probe_timeout_ms: int | None = None,
    expected_domain: str | None = None,
    scope_selector: str | None = None,
    *,
    config: Annotated[RunnableConfig, InjectedToolArg] = None,
    state: Annotated[dict[str, Any], InjectedState] = None,
) -> str:
    """Pause for user input; batch isolation enforced by GateSoloBatchMiddleware."""
    global GATE_NODE_ENTRY_COUNT
    GATE_NODE_ENTRY_COUNT += 1

    conversation_id = _conversation_id_from_config(config)
    workspace_path = (config.get("configurable") or {}).get("workspace") or "/tmp"

    workspace = Path(workspace_path)
    gate_id = uuid.uuid4().hex
    field_defs = normalize_field_defs(list(fields or []))
    turn_id = _turn_id_from_config(config)
    method_selected = await turn_auth.is_method_selected(conversation_id, turn_id) if turn_id else False
    if not method_selected:
        domain_hint = await _resolve_gate_domain(
            conversation_id=conversation_id,
            expected_domain=expected_domain,
        )
        if domain_hint:
            method_selected = await site_auth.is_login_method_selected(conversation_id, domain_hint)
    normalized_choices = _normalize_choices(choices)
    probe_timeout = (
        login_probe_timeout_ms
        if login_probe_timeout_ms is not None
        else settings.CHAT_LOGIN_PROBE_TIMEOUT_MS
    )

    preflight = _validate_gate_before_interrupt(
        gate_type=gate_type,
        phase=phase,
        field_defs=field_defs,
        username_selector=username_selector,
        password_selector=password_selector,
        submit_selector=submit_selector,
        phone_selector=phone_selector,
        code_selector=code_selector,
        captcha_image_selector=captcha_image_selector,
        captcha_input_selector=captcha_input_selector,
        qr_image_selector=qr_image_selector,
        expected_domain=expected_domain,
        choices=normalized_choices if gate_type == "login_method" else choices,
        method_selected=method_selected,
    )
    if preflight is not None:
        return preflight.to_tool_message()

    otp_flow_id: str | None = None
    if gate_type == "phone_otp" and phase == "phone":
        otp_flow_id = await otp_flow.start_otp_flow(conversation_id)

    gate_assets_payload: dict[str, object] = {}
    image_selector: str | None = None
    asset_kind: str | None = None
    refresh = False
    qr_verify_suffix: str | None = None

    if gate_type == "qr_scan":
        image_selector = qr_image_selector
        asset_kind = "qr"
        refresh = True
    elif gate_type == "image_captcha":
        image_selector = captcha_image_selector
        asset_kind = "captcha"
        refresh = False

    if image_selector:
        try:
            _, verify_suffix = await gate_assets.capture_gate_image(
                conversation_id=conversation_id,
                workspace=workspace,
                gate_id=gate_id,
                image_selector=image_selector,
                asset_kind=asset_kind,
            )
        except GateCaptureError as exc:
            return exc.to_tool_result().to_tool_message()
        except Exception as exc:
            return ToolResult.fail(BROWSER_ERROR, detail=str(exc)).to_tool_message()
        gate_assets_payload = gate_assets.build_assets_payload(
            conversation_id=conversation_id,
            gate_id=gate_id,
            asset_kind=asset_kind or "qr",
            refresh_interval_sec=3 if refresh else 0,
        )
        if verify_suffix and asset_kind == "qr":
            qr_verify_suffix = verify_suffix

    turn_id = str((config.get("configurable") or {}).get("turn_id") or "")

    await gate_meta.set_gate_meta(
        gate_id,
        conversation_id=conversation_id,
        turn_id=turn_id,
        gate_type=gate_type,
        asset_kind=asset_kind,
        image_selector=image_selector,
        captcha_input_selector=captcha_input_selector,
        refresh=refresh,
        phase=phase,
        otp_flow_id=otp_flow_id,
        username_selector=username_selector,
        password_selector=password_selector,
        submit_selector=submit_selector,
        phone_selector=phone_selector,
        code_selector=code_selector,
        qr_image_selector=qr_image_selector,
        login_probe_selector=login_probe_selector,
        expected_domain=expected_domain,
        choices=normalized_choices if gate_type == "login_method" else None,
    )

    gate_payload: dict[str, Any] = strip_public_gate_payload(
        {
            "gate_id": gate_id,
            "gate_type": gate_type,
            "prompt": prompt,
            "fields": field_defs,
            "assets": gate_assets_payload,
            "choices": normalized_choices if gate_type == "login_method" else None,
        }
    )
    if phase is not None:
        gate_payload["phase"] = phase
    if gate_type == "session_bridge":
        gate_payload["domain"] = expected_domain

    resume_value = interrupt(gate_payload)

    if not isinstance(resume_value, dict):
        return ToolResult.fail(INVALID_ARGUMENTS, detail="invalid resume payload").to_tool_message()

    action = str(resume_value.get("action") or "")
    if action == "cancel":
        await _release_gate(workspace, gate_id)
        if turn_id:
            await turn_auth.clear_turn_auth(conversation_id, turn_id)
        return ToolResult.fail(GATE_CANCELLED, detail="user cancelled gate").to_tool_message()
    if action != "submit":
        return ToolResult.fail(INVALID_ARGUMENTS, detail=f"unknown action: {action}").to_tool_message()

    if gate_type == "login_method":
        submitted = resume_value.get("fields")
        if not isinstance(submitted, dict):
            return ToolResult.fail(INVALID_ARGUMENTS, detail="missing fields on submit").to_tool_message()
        choice_id = str(submitted.get("choice_id") or "").strip()
        meta = await gate_meta.get_gate_meta(gate_id) or {}
        stored_choices = meta.get("choices") if isinstance(meta.get("choices"), list) else normalized_choices
        choice = _choice_by_id(list(stored_choices or []), choice_id)
        if choice is None:
            return ToolResult.fail(INVALID_ARGUMENTS, detail="invalid choice_id").to_tool_message()
        tab_selector = str(choice.get("tab_selector") or "").strip()
        tab_click_dispatched = False
        if tab_selector:
            click_result = await browser_runtime.click_selector(
                conversation_id=conversation_id,
                workspace=workspace,
                selector=tab_selector,
                scope_selector=scope_selector,
            )
            if not click_result.success:
                await _release_gate(workspace, gate_id)
                return click_result.to_tool_message()
            tab_click_dispatched = True
        target = str(choice.get("target_gate") or "")
        if turn_id:
            await turn_auth.set_method_selected(
                conversation_id=conversation_id,
                turn_id=turn_id,
                choice_id=choice_id,
                target_gate=target,
            )
        domain = await _resolve_gate_domain(
            conversation_id=conversation_id,
            expected_domain=expected_domain,
        )
        if domain:
            await site_auth.mark_login_method_selected(
                conversation_id=conversation_id,
                domain=domain,
                choice_id=choice_id,
                target_gate=target,
            )
        await _release_gate(workspace, gate_id)
        result_payload: dict[str, Any] = {
            "status": "choice_submitted",
            "choice_id": choice_id,
            "target_gate": target,
        }
        if tab_click_dispatched:
            result_payload["tab_click_dispatched"] = True
        return ToolResult.ok(json.dumps(result_payload, ensure_ascii=False)).to_tool_message()

    if gate_type in ("confirm",):
        await _release_gate(workspace, gate_id)
        return ToolResult.ok('{"status":"user_completed"}').to_tool_message()

    if gate_type == "session_bridge":
        result = await _apply_session_bridge_cookies(
            conversation_id=conversation_id,
            workspace=workspace,
            gate_id=gate_id,
        )
        await _release_gate(workspace, gate_id)
        return result.to_tool_message()

    submitted = resume_value.get("fields")
    if gate_type != "qr_scan":
        if not isinstance(submitted, dict):
            return ToolResult.fail(INVALID_ARGUMENTS, detail="missing fields on submit").to_tool_message()
    else:
        submitted = submitted if isinstance(submitted, dict) else {}

    await vault.put(gate_id, submitted)
    secrets = await vault.take(gate_id)
    if secrets is None and gate_type != "qr_scan":
        return ToolResult.fail(INVALID_ARGUMENTS, detail="vault miss").to_tool_message()
    secrets = secrets or {}

    params = GateActionParams(
        gate_type=gate_type,
        phase=phase,
        field_defs=field_defs,
        username_selector=username_selector,
        password_selector=password_selector,
        submit_selector=submit_selector,
        phone_selector=phone_selector,
        code_selector=code_selector,
        captcha_input_selector=captcha_input_selector,
        login_probe_selector=login_probe_selector,
        login_probe_timeout_ms=probe_timeout,
    )

    if gate_type == "phone_otp" and phase == "phone":
        result = await browser_runtime.apply_gate_resume(
            conversation_id=conversation_id,
            workspace=workspace,
            secrets=secrets,
            params=params,
        )
        if not result.success:
            await _release_gate(workspace, gate_id)
            return result.to_tool_message()
        if otp_flow_id:
            await otp_flow.mark_phone_submitted(conversation_id, otp_flow_id)
        domain = await _resolve_gate_domain(
            conversation_id=conversation_id,
            expected_domain=expected_domain,
        )
        if domain:
            await site_auth.mark_phone_otp_flow_started(
                conversation_id=conversation_id,
                domain=domain,
            )
        await _release_gate(workspace, gate_id)
        return ToolResult.ok(
            json.dumps({"status": "phone_submitted", "otp_flow_id": otp_flow_id}, ensure_ascii=False)
        ).to_tool_message()

    if gate_type == "qr_scan":
        await _release_gate(workspace, gate_id)
        payload: dict[str, Any] = {"status": "qr_acknowledged"}
        if qr_verify_suffix:
            try:
                payload = {**json.loads(qr_verify_suffix), **payload}
            except json.JSONDecodeError:
                pass
        return ToolResult.ok(json.dumps(payload, ensure_ascii=False)).to_tool_message()

    result = await browser_runtime.apply_gate_resume(
        conversation_id=conversation_id,
        workspace=workspace,
        secrets=secrets,
        params=params,
    )
    domain = await _resolve_gate_domain(
        conversation_id=conversation_id,
        expected_domain=expected_domain,
    )
    if result.success and domain and gate_type == "credentials":
        await site_auth.mark_credentials_submitted(
            conversation_id=conversation_id,
            domain=domain,
        )
    await _release_gate(workspace, gate_id)
    return result.to_tool_message()
