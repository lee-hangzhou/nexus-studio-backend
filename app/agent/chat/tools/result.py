from __future__ import annotations

import json

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.agent.runtime.turn.tool_loop_guard import TOOL_LOOP_EXHAUSTED

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


class ToolResultProtocolError(ValueError):
    """Raised when a tool message violates the single supported result contract."""


class ToolResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    success: bool
    output: str
    error_type: str | None = Field(default=None, min_length=1)
    error_detail: str | None = None

    @model_validator(mode="after")
    def validate_outcome(self) -> "ToolResult":
        if self.success:
            if not self.output:
                raise ValueError("successful tool result requires non-empty output")
            if self.error_type is not None or self.error_detail is not None:
                raise ValueError("successful tool result cannot contain error fields")
        elif self.error_type is None:
            raise ValueError("failed tool result requires error_type")
        return self

    @classmethod
    def ok(cls, output: str) -> ToolResult:
        return cls(success=True, output=output)

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
        payload = self.model_dump(mode="json")
        return json.dumps({"tool_result": payload}, ensure_ascii=False)

    @classmethod
    def parse_tool_message(cls, content: str) -> ToolResult:
        if not isinstance(content, str):
            raise ToolResultProtocolError("tool result must be a JSON string")
        text = content.strip()
        if not text:
            raise ToolResultProtocolError("tool result cannot be empty")
        try:
            raw = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ToolResultProtocolError("tool result is not valid JSON") from exc
        if not isinstance(raw, dict) or set(raw) != {"tool_result"}:
            raise ToolResultProtocolError("tool result envelope must contain only tool_result")
        try:
            return cls.model_validate(raw["tool_result"])
        except ValidationError as exc:
            raise ToolResultProtocolError("tool result payload violates contract") from exc

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
