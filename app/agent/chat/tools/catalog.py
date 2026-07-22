"""Browser tool interaction_kind catalog (documentation + logging; not orchestrator routing)."""

from __future__ import annotations

from app.agent.chat.contracts.interaction import InteractionKind

BROWSER_EXEC_SCRIPT = "browser_exec_script"
BROWSER_CAPTURE_STATE = "browser_capture_state"
REQUEST_USER_GATE = "request_user_gate"
BROWSER_TRIGGER_OTP_SEND = "browser_trigger_otp_send"
BROWSER_RESTORE_SESSION = "browser_restore_session"
BROWSER_END_SESSION = "browser_end_session"
BROWSER_AUTH_STATUS = "browser_auth_status"
SIGNAL_BROWSER_BLOCKED = "signal_browser_blocked"
BROWSER_CHALLENGE_READ_GEOMETRY = "browser_challenge_read_geometry"
BROWSER_CHALLENGE_SCREENSHOT_ELEMENT = "browser_challenge_screenshot_element"
BROWSER_CHALLENGE_DISPATCH_POINTER_TRACE = "browser_challenge_dispatch_pointer_trace"
BROWSER_CHALLENGE_WAIT_PROBE = "browser_challenge_wait_probe"
CV_IMAGE_INFO = "cv_image_info"
CV_TEMPLATE_MATCH = "cv_template_match"
CV_FIND_GAP_X = "cv_find_gap_x"

TOOL_INTERACTION_KIND: dict[str, InteractionKind] = {
    BROWSER_EXEC_SCRIPT: InteractionKind.WRITE,
    BROWSER_CAPTURE_STATE: InteractionKind.READ,
    REQUEST_USER_GATE: InteractionKind.WRITE,
    BROWSER_TRIGGER_OTP_SEND: InteractionKind.WRITE,
    BROWSER_RESTORE_SESSION: InteractionKind.READ,
    BROWSER_END_SESSION: InteractionKind.WRITE,
    BROWSER_AUTH_STATUS: InteractionKind.READ,
    SIGNAL_BROWSER_BLOCKED: InteractionKind.READ,
    BROWSER_CHALLENGE_READ_GEOMETRY: InteractionKind.READ,
    BROWSER_CHALLENGE_SCREENSHOT_ELEMENT: InteractionKind.READ,
    BROWSER_CHALLENGE_DISPATCH_POINTER_TRACE: InteractionKind.WRITE,
    BROWSER_CHALLENGE_WAIT_PROBE: InteractionKind.READ,
    CV_IMAGE_INFO: InteractionKind.READ,
    CV_TEMPLATE_MATCH: InteractionKind.READ,
    CV_FIND_GAP_X: InteractionKind.READ,
}


def interaction_kind_for(tool_name: str) -> InteractionKind | None:
    return TOOL_INTERACTION_KIND.get(tool_name)
