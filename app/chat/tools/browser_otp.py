"""Trigger OTP send button click — separate step from request_user_gate."""

from __future__ import annotations

import json
from typing import Annotated

from langchain.tools import InjectedToolArg
from langchain_core.runnables import RunnableConfig

from app.chat.browser import runtime as browser_runtime
from app.chat.gate import otp_flow
from app.chat.tools.result import (
    BROWSER_ERROR,
    INVALID_ARGUMENTS,
    OTP_ALREADY_SENT,
    ToolResult,
)
from app.core.logger import logger


def _conversation_id_from_config(config: RunnableConfig | None) -> int:
    if not config:
        raise ValueError("missing runnable config")
    raw = (config.get("configurable") or {}).get("conversation_id")
    if raw is None:
        raise ValueError("missing conversation_id in config")
    return int(raw)


async def browser_trigger_otp_send(
    send_selector: str,
    otp_flow_id: str | None = None,
    *,
    config: Annotated[RunnableConfig, InjectedToolArg] = None,
) -> str:
    """Click the send-SMS button for an active phone_otp flow."""
    conversation_id = _conversation_id_from_config(config)
    if not send_selector:
        return ToolResult.fail(
            INVALID_ARGUMENTS,
            detail="send_selector is required",
        ).to_tool_message()

    flow_id = (otp_flow_id or "").strip()
    if not flow_id:
        flow_id = await otp_flow.start_otp_flow(conversation_id)
        logger.info(
            "otp_begin_flow",
            conversation_id=conversation_id,
            otp_flow_id=flow_id,
        )
    else:
        flow = await otp_flow.get_otp_flow(conversation_id, flow_id)
        if flow is None:
            return ToolResult.fail(INVALID_ARGUMENTS, detail="unknown otp_flow_id").to_tool_message()

    first_send = await otp_flow.mark_otp_sent(conversation_id, flow_id, send_selector)
    if not first_send:
        return ToolResult.fail(OTP_ALREADY_SENT, detail="send already clicked for this flow").to_tool_message()

    await otp_flow.set_otp_send_selector(conversation_id, flow_id, send_selector)

    from pathlib import Path

    workspace_path = (config.get("configurable") or {}).get("workspace") or "/tmp"
    selector_json = json.dumps(send_selector)
    code = f"await page.locator({selector_json}).click()\nprint(json.dumps({{'status': 'otp_send_clicked'}}))"
    result = await browser_runtime.exec_user_script(
        conversation_id=conversation_id,
        workspace=Path(workspace_path),
        code=code,
    )
    if not result.success:
        return result.to_tool_message()

    logger.info(
        "otp_send_clicked",
        conversation_id=conversation_id,
        otp_flow_id=flow_id,
    )
    return ToolResult.ok(
        json.dumps({"status": "otp_send_clicked", "otp_flow_id": flow_id})
    ).to_tool_message()
