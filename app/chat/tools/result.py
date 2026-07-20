from __future__ import annotations

import json
from dataclasses import dataclass

from app.core.turn.tool_loop_guard import TOOL_LOOP_EXHAUSTED

# Stable error_type values (also used as error_class in runner/guards).
INVALID_ARGUMENTS = "invalid_arguments"
FILE_NOT_READY = "file_not_ready"
FILE_NOT_FOUND = "file_not_found"
SANDBOX_ERROR = "sandbox_error"
SANDBOX_TIMEOUT = "sandbox_timeout"
SANDBOX_UNAVAILABLE = "sandbox_unavailable"
DUPLICATE_CALL = "duplicate_call"
INTERNAL = "internal"
TURN_INTERRUPTED = "turn_interrupted"
WEB_UNAVAILABLE = "web_unavailable"
MEMORY_UNAVAILABLE = "memory_unavailable"
INVALID_URL = "invalid_url"
BROWSER_ERROR = "browser_error"
BROWSER_TIMEOUT = "browser_timeout"
BROWSER_UNAVAILABLE = "browser_unavailable"
BROWSER_BLOCKED = "browser_blocked"
GATE_CANCELLED = "gate_cancelled"
GATE_SETUP_FAILED = "gate_setup_failed"
GATE_EXPIRED = "gate_expired"
GATE_BATCH_ISOLATION = "gate_batch_isolation"
LOGIN_METHOD_REQUIRED = "login_method_required"
LOGIN_FAILED = "login_failed"
MISSING_SELECTOR = "missing_selector"
QR_ELEMENT_NOT_VISIBLE = "qr_element_not_visible"
QR_ASSET_INVALID = "qr_asset_invalid"
QR_NOT_DECODABLE = "qr_not_decodable"
OTP_ALREADY_SENT = "otp_already_sent"
CHALLENGE_ELEMENT_NOT_FOUND = "challenge_element_not_found"
CHALLENGE_GEOMETRY_INVALID = "challenge_geometry_invalid"
CV_LOW_CONFIDENCE = "cv_low_confidence"
PROBE_TIMEOUT = "probe_timeout"
POINTER_DISPATCH_FAILED = "pointer_dispatch_failed"
CHALLENGE_OUTCOME_FAILED = "challenge_outcome_failed"
CHALLENGE_OUTCOME_DISMISSED = "challenge_outcome_dismissed"
CHALLENGE_OUTCOME_INCONCLUSIVE = "challenge_outcome_inconclusive"


@dataclass(frozen=True)
class ToolResult:
    success: bool
    output: str
    error_type: str | None = None
    error_detail: str | None = None

    @classmethod
    def ok(cls, output: str) -> ToolResult:
        return cls(success=True, output=output or "")

    @classmethod
    def fail(
        cls,
        error_type: str,
        detail: str | None = None,
        *,
        output: str = "",
    ) -> ToolResult:
        return cls(
            success=False,
            output=output,
            error_type=error_type,
            error_detail=detail,
        )

    def to_tool_message(self) -> str:
        payload = {
            "success": self.success,
            "output": self.output,
            "error_type": self.error_type,
            "error_detail": self.error_detail,
        }
        return json.dumps({"tool_result": payload}, ensure_ascii=False)

    @classmethod
    def parse_tool_message(cls, content: str) -> ToolResult:
        text = (content or "").strip()
        if not text:
            return cls.ok("")

        if text.startswith("{"):
            try:
                raw = json.loads(text)
            except json.JSONDecodeError:
                return cls.ok(text)
            if isinstance(raw, dict) and "tool_result" in raw:
                item = raw["tool_result"]
                if isinstance(item, dict):
                    return cls(
                        success=bool(item.get("success")),
                        output=str(item.get("output") or ""),
                        error_type=item.get("error_type"),
                        error_detail=item.get("error_detail"),
                    )

        # Legacy plain-text tool outputs (pre-ToolResult).
        if text.startswith("tool_error: invalid_arguments"):
            return cls.fail(INVALID_ARGUMENTS, detail=text)
        if text.startswith("tool_error: duplicate_call"):
            return cls.fail(DUPLICATE_CALL, detail=text)
        if text.startswith("tool_error:"):
            return cls.fail(INTERNAL, detail=text)
        if text.startswith("file not ready:"):
            return cls.fail(FILE_NOT_READY, detail=text, output=text)
        if text.startswith("file not found:") or text.startswith("publish_file: file not found"):
            return cls.fail(FILE_NOT_FOUND, detail=text, output=text)
        if text.startswith("execute_python: timeout"):
            return cls.fail(SANDBOX_TIMEOUT, detail=text, output=text)
        if text.startswith("execute_python: docker not available"):
            return cls.fail(SANDBOX_UNAVAILABLE, detail=text, output=text)
        if text.startswith("exit=") and "stderr:" in text:
            return cls.fail(SANDBOX_ERROR, detail=text, output=text)

        return cls.ok(text)

    @property
    def display_text(self) -> str:
        if self.success:
            return self.output
        parts = [self.output] if self.output else []
        if self.error_detail:
            parts.append(self.error_detail)
        if self.error_type:
            parts.append(f"error_type={self.error_type}")
        return "\n".join(parts) if parts else f"error_type={self.error_type}"
