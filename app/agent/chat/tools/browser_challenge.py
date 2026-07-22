"""Browser challenge mechanism tools (geometry, screenshot, pointer trace, probe)."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Annotated, Any

from langchain.tools import InjectedToolArg
from langchain_core.runnables import RunnableConfig

from app.agent.chat.browser.challenge import artifacts, mechanisms
from app.agent.chat.contracts.interaction import ProbeOutcome, probe_outcome_error_type
from app.agent.chat.tools.result import (
    BROWSER_ERROR,
    BROWSER_UNAVAILABLE,
    CHALLENGE_ELEMENT_NOT_FOUND,
    CHALLENGE_GEOMETRY_INVALID,
    INVALID_ARGUMENTS,
    POINTER_DISPATCH_FAILED,
    ToolResult,
)


def _conversation_id_from_config(config: RunnableConfig | None) -> int:
    if not config:
        raise ValueError("missing runnable config")
    raw = (config.get("configurable") or {}).get("conversation_id")
    if raw is None:
        raise ValueError("missing conversation_id in config")
    return int(raw)


def _workspace_from_config(config: RunnableConfig | None) -> Path:
    return Path((config.get("configurable") or {}).get("workspace") or "/tmp")


def _resolve_attempt_id(attempt_id: str | None) -> str:
    return attempt_id or uuid.uuid4().hex


def _probe_tool_result(payload: dict[str, Any]) -> ToolResult:
    outcome = str(payload.get("outcome") or ProbeOutcome.INCONCLUSIVE.value)
    body = json.dumps(payload, ensure_ascii=False)
    if outcome == ProbeOutcome.PASSED.value:
        return ToolResult.ok(body)
    error_type = probe_outcome_error_type(outcome)
    if error_type is None:
        return ToolResult.ok(body)
    return ToolResult.fail(error_type, detail=body, output=body)


async def browser_challenge_read_geometry(
    container_selector: str,
    track_selector: str,
    handle_selector: str,
    attempt_id: str | None = None,
    scope_selector: str | None = None,
    content_selector: str | None = None,
    *,
    config: Annotated[RunnableConfig, InjectedToolArg] = None,
) -> str:
    """Read C/T/H bounding boxes and travel range in css_viewport coordinates."""
    conversation_id = _conversation_id_from_config(config)
    workspace = _workspace_from_config(config)
    aid = _resolve_attempt_id(attempt_id)
    try:
        payload = await mechanisms.read_geometry(
            conversation_id=conversation_id,
            workspace=workspace,
            container_selector=container_selector,
            track_selector=track_selector,
            handle_selector=handle_selector,
            attempt_id=aid,
            scope_selector=scope_selector,
            content_selector=content_selector,
        )
        payload["attempt_id"] = aid
        return ToolResult.ok(json.dumps(payload, ensure_ascii=False)).to_tool_message()
    except ValueError as exc:
        detail = str(exc)
        if detail.startswith("challenge_geometry_invalid"):
            return ToolResult.fail(CHALLENGE_GEOMETRY_INVALID, detail=detail).to_tool_message()
        return ToolResult.fail(INVALID_ARGUMENTS, detail=detail).to_tool_message()
    except RuntimeError as exc:
        detail = str(exc)
        if "not found" in detail.lower():
            return ToolResult.fail(CHALLENGE_ELEMENT_NOT_FOUND, detail=detail).to_tool_message()
        return ToolResult.fail(BROWSER_ERROR, detail=detail).to_tool_message()
    except Exception as exc:
        return ToolResult.fail(BROWSER_UNAVAILABLE, detail=str(exc)).to_tool_message()


async def browser_challenge_screenshot_element(
    selector: str,
    path: str,
    attempt_id: str | None = None,
    scope_selector: str | None = None,
    *,
    config: Annotated[RunnableConfig, InjectedToolArg] = None,
) -> str:
    """Screenshot a DOM element to a workspace-relative path; returns bbox in css_viewport."""
    conversation_id = _conversation_id_from_config(config)
    workspace = _workspace_from_config(config)
    aid = _resolve_attempt_id(attempt_id)
    try:
        payload = await mechanisms.screenshot_element(
            conversation_id=conversation_id,
            workspace=workspace,
            selector=selector,
            path=path,
            attempt_id=aid,
            scope_selector=scope_selector,
        )
        payload["attempt_id"] = aid
        return ToolResult.ok(json.dumps(payload, ensure_ascii=False)).to_tool_message()
    except RuntimeError as exc:
        detail = str(exc)
        if "not found" in detail.lower():
            return ToolResult.fail(CHALLENGE_ELEMENT_NOT_FOUND, detail=detail).to_tool_message()
        return ToolResult.fail(BROWSER_ERROR, detail=detail).to_tool_message()
    except Exception as exc:
        return ToolResult.fail(BROWSER_UNAVAILABLE, detail=str(exc)).to_tool_message()


async def browser_challenge_dispatch_pointer_trace(
    events: list[dict[str, Any]],
    attempt_id: str | None = None,
    *,
    config: Annotated[RunnableConfig, InjectedToolArg] = None,
) -> str:
    """Dispatch pointer down/move/up events in css_viewport pixel coordinates."""
    conversation_id = _conversation_id_from_config(config)
    workspace = _workspace_from_config(config)
    aid = _resolve_attempt_id(attempt_id)
    try:
        payload = await mechanisms.dispatch_pointer_trace(
            conversation_id=conversation_id,
            workspace=workspace,
            events=events,
            attempt_id=aid,
        )
        payload["attempt_id"] = aid
        return ToolResult.ok(json.dumps(payload, ensure_ascii=False)).to_tool_message()
    except ValueError as exc:
        return ToolResult.fail(POINTER_DISPATCH_FAILED, detail=str(exc)).to_tool_message()
    except Exception as exc:
        return ToolResult.fail(BROWSER_UNAVAILABLE, detail=str(exc)).to_tool_message()


async def browser_challenge_wait_probe(
    selector: str | None = None,
    success_selector: str | None = None,
    failure_selector: str | None = None,
    retry_text_probe: str | None = None,
    panel_selector: str | None = None,
    scope_selector: str | None = None,
    timeout_ms: int = 5000,
    attempt_id: str | None = None,
    *,
    config: Annotated[RunnableConfig, InjectedToolArg] = None,
) -> str:
    """Observe challenge outcome via success/failure/panel signals after interaction."""
    conversation_id = _conversation_id_from_config(config)
    workspace = _workspace_from_config(config)
    aid = _resolve_attempt_id(attempt_id)
    resolved_success = success_selector or selector
    if not resolved_success:
        return ToolResult.fail(
            INVALID_ARGUMENTS,
            detail="wait_probe requires success_selector or selector",
        ).to_tool_message()
    try:
        payload = await mechanisms.wait_probe(
            conversation_id=conversation_id,
            workspace=workspace,
            selector=resolved_success,
            success_selector=resolved_success,
            failure_selector=failure_selector,
            retry_text_probe=retry_text_probe,
            panel_selector=panel_selector,
            scope_selector=scope_selector,
            timeout_ms=timeout_ms,
            attempt_id=aid,
        )
        payload["attempt_id"] = aid
        result = _probe_tool_result(payload)
        if not result.success:
            artifacts.cleanup_attempt(workspace, aid)
        else:
            artifacts.cleanup_attempt(workspace, aid)
        return result.to_tool_message()
    except Exception as exc:
        if aid:
            artifacts.cleanup_attempt(workspace, aid)
        return ToolResult.fail(BROWSER_UNAVAILABLE, detail=str(exc)).to_tool_message()
