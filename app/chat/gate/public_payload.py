"""Strip backend-only fields from gate payloads sent to SSE / frontend."""

from __future__ import annotations

from typing import Any

_STRIP_EXACT = frozenset(
    {
        "otp_flow_id",
        "bridge_token",
        "page_region",
        "captured_page_region",
        "send_selector",
        "challenge_selector",
        "login_probe_selector",
        "username_selector",
        "password_selector",
        "submit_selector",
        "phone_selector",
        "code_selector",
        "captcha_image_selector",
        "captcha_input_selector",
        "qr_image_selector",
        "image_selector",
        "target_gate",
        "tab_selector",
    }
)


def _should_strip_key(key: str) -> bool:
    if key in _STRIP_EXACT:
        return True
    return key.endswith("_selector")


def strip_public_gate_payload(payload: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in payload.items():
        if _should_strip_key(key):
            continue
        if key == "choices" and isinstance(value, list):
            out[key] = public_login_choices(value)
        elif key == "assets" and isinstance(value, dict):
            out[key] = strip_public_gate_payload(value)
        elif isinstance(value, dict):
            out[key] = strip_public_gate_payload(value)
        elif isinstance(value, list):
            out[key] = value
        else:
            out[key] = value
    return out


def public_login_choices(choices: list[Any]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for item in choices:
        if not isinstance(item, dict):
            continue
        choice_id = str(item.get("id") or "").strip()
        label = str(item.get("label") or "").strip()
        if choice_id and label:
            out.append({"id": choice_id, "label": label})
    return out
