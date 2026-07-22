"""Stable browser tool names shared by guards, frontend, and previews."""

from __future__ import annotations

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

BROWSER_TOOL_NAMES: frozenset[str] = frozenset(
    {
        BROWSER_EXEC_SCRIPT,
        BROWSER_CAPTURE_STATE,
        REQUEST_USER_GATE,
        BROWSER_TRIGGER_OTP_SEND,
        BROWSER_RESTORE_SESSION,
        BROWSER_END_SESSION,
        BROWSER_AUTH_STATUS,
        SIGNAL_BROWSER_BLOCKED,
        BROWSER_CHALLENGE_READ_GEOMETRY,
        BROWSER_CHALLENGE_SCREENSHOT_ELEMENT,
        BROWSER_CHALLENGE_DISPATCH_POINTER_TRACE,
        BROWSER_CHALLENGE_WAIT_PROBE,
        CV_IMAGE_INFO,
        CV_TEMPLATE_MATCH,
        CV_FIND_GAP_X,
    }
)
