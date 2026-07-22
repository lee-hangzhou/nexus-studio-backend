"""Cross-layer interaction contracts: write-verify, probe outcomes, auth facts."""

from __future__ import annotations

from enum import Enum
from typing import Any, TypedDict

from app.agent.chat.tools.result import (
    CHALLENGE_OUTCOME_DISMISSED,
    CHALLENGE_OUTCOME_FAILED,
    CHALLENGE_OUTCOME_INCONCLUSIVE,
)


class InteractionKind(str, Enum):
    READ = "read"
    WRITE = "write"


class ProbeOutcome(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    DISMISSED = "dismissed"
    INCONCLUSIVE = "inconclusive"


class ScaleFacts(TypedDict, total=False):
    content_space: str
    track_width_css: float
    content_width_px: float | None
    css_per_intrinsic_x: float | None


class AuthProbeFacts(TypedDict, total=False):
    logged_in: bool
    need_login: bool
    domain: str | None
    restored: bool
    auth_flags: dict[str, Any]


class ChallengeProbeResult(TypedDict, total=False):
    outcome: str
    verified: bool
    observations: dict[str, Any]
    url_before: str
    url_after: str
    selector: str
    error: str | None


def probe_outcome_error_type(outcome: str) -> str | None:
    """Map probe outcome to stable error_type; None when passed."""
    if outcome == ProbeOutcome.PASSED.value:
        return None
    if outcome == ProbeOutcome.FAILED.value:
        return CHALLENGE_OUTCOME_FAILED
    if outcome == ProbeOutcome.DISMISSED.value:
        return CHALLENGE_OUTCOME_DISMISSED
    return CHALLENGE_OUTCOME_INCONCLUSIVE
