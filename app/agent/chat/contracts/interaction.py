"""跨层交互契约: 写后校验、探测结果、鉴权事实"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

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


class ScaleFacts(BaseModel):
    """挑战几何缩放事实"""

    content_space: str
    track_width_css: float
    content_width_px: float | None = None
    css_per_intrinsic_x: float | None = None


class AuthProbeFacts(BaseModel):
    """站点鉴权探测事实"""

    logged_in: bool | None = None
    need_login: bool | None = None
    domain: str | None = None
    restored: bool | None = None
    auth_flags: dict[str, Any] = Field(default_factory=dict)
    reason: str | None = None


class ChallengeProbeResult(BaseModel):
    """挑战探测结果"""

    outcome: str
    verified: bool
    observations: dict[str, Any] = Field(default_factory=dict)
    visible: bool = False
    url_before: str = ""
    url_after: str = ""
    selector: str = ""
    error: str | None = None


def probe_outcome_error_type(outcome: str) -> str | None:
    """把探测 outcome 映射为稳定 error_type; passed 时返回 None"""
    if outcome == ProbeOutcome.PASSED.value:
        return None
    if outcome == ProbeOutcome.FAILED.value:
        return CHALLENGE_OUTCOME_FAILED
    if outcome == ProbeOutcome.DISMISSED.value:
        return CHALLENGE_OUTCOME_DISMISSED
    return CHALLENGE_OUTCOME_INCONCLUSIVE
