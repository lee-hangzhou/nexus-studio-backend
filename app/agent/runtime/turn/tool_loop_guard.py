from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from app.server.infra.logger import logger
from app.server.skills.domain.enums import SkillSurface

TOOL_LOOP_EXHAUSTED = "tool_loop_exhausted"


class ToolLoopTrack(str, Enum):
    EMPTY_STREAK = "empty_streak"
    DUPLICATE_ARGS = "duplicate_args"
    ONCE_PER_TURN = "once_per_turn"


@dataclass(frozen=True)
class ToolLoopPolicy:
    track: ToolLoopTrack
    max_empty_before_block: int = 2
    max_duplicate_before_block: int = 1


TOOL_LOOP_POLICIES: dict[str, ToolLoopPolicy] = {
    "recall_user_memory": ToolLoopPolicy(track=ToolLoopTrack.ONCE_PER_TURN),
    "recall_project_memory": ToolLoopPolicy(track=ToolLoopTrack.ONCE_PER_TURN),
    "list_generate_models": ToolLoopPolicy(track=ToolLoopTrack.DUPLICATE_ARGS),
}

ONCE_PER_TURN_TOOL_NAMES = frozenset(
    name for name, policy in TOOL_LOOP_POLICIES.items() if policy.track == ToolLoopTrack.ONCE_PER_TURN
)


@dataclass(frozen=True)
class LoopBlockInfo:
    reason: str
    tool_name: str
    attempt: int
    streak: int

    def detail(self) -> str:
        return json.dumps(
            {
                "reason": self.reason,
                "tool_name": self.tool_name,
                "attempt": self.attempt,
                "streak": self.streak,
            },
            ensure_ascii=False,
        )


@dataclass
class TurnToolLoopGuard:
    """Per-turn tool loop guard; create a new instance each turn."""

    surface: str = SkillSurface.CHAT
    _empty_streaks: dict[str, int] = field(default_factory=dict)
    _successful_calls: dict[str, int] = field(default_factory=dict)
    _successful_fingerprints: set[str] = field(default_factory=set)

    def pre_check(self, tool_name: str, *, args: dict[str, Any] | None = None) -> LoopBlockInfo | None:
        policy = TOOL_LOOP_POLICIES.get(tool_name)
        if policy is None:
            return None

        if policy.track == ToolLoopTrack.EMPTY_STREAK:
            streak = self._empty_streaks.get(tool_name, 0)
            if streak >= policy.max_empty_before_block:
                logger.info(
                    "tool_loop_guard.triggered",
                    tool_name=tool_name,
                    reason="empty_success_streak",
                    streak=streak,
                    attempt=streak + 1,
                    surface=self.surface,
                )
                return LoopBlockInfo(
                    reason="empty_success_streak",
                    tool_name=tool_name,
                    attempt=streak + 1,
                    streak=streak,
                )

        if policy.track == ToolLoopTrack.ONCE_PER_TURN:
            prior = self._successful_calls.get(tool_name, 0)
            if prior >= 1:
                logger.info(
                    "tool_loop_guard.triggered",
                    tool_name=tool_name,
                    reason="once_per_turn",
                    streak=prior,
                    attempt=prior + 1,
                    surface=self.surface,
                )
                return LoopBlockInfo(
                    reason="once_per_turn",
                    tool_name=tool_name,
                    attempt=prior + 1,
                    streak=prior,
                )
            self._successful_calls[tool_name] = 1

        if policy.track == ToolLoopTrack.DUPLICATE_ARGS and args is not None:
            fingerprint = _fingerprint(tool_name, args)
            if fingerprint in self._successful_fingerprints:
                logger.info(
                    "tool_loop_guard.triggered",
                    tool_name=tool_name,
                    reason="duplicate_args",
                    streak=0,
                    attempt=2,
                    surface=self.surface,
                )
                return LoopBlockInfo(
                    reason="duplicate_args",
                    tool_name=tool_name,
                    attempt=2,
                    streak=0,
                )

        return None

    def record(
        self,
        tool_name: str,
        *,
        args: dict[str, Any] | None,
        success: bool,
        is_empty: bool,
    ) -> None:
        if not success:
            return
        policy = TOOL_LOOP_POLICIES.get(tool_name)
        if policy is None:
            return

        if policy.track == ToolLoopTrack.EMPTY_STREAK:
            if is_empty:
                self._empty_streaks[tool_name] = self._empty_streaks.get(tool_name, 0) + 1
            else:
                self._empty_streaks.pop(tool_name, None)

        if policy.track == ToolLoopTrack.DUPLICATE_ARGS and args is not None:
            self._successful_fingerprints.add(_fingerprint(tool_name, args))


def loop_args_from_kwargs(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Strip LangChain injected keys; keep model-visible args for fingerprinting."""
    skip = {"config", "tool_call_id", "run_manager", "callbacks"}
    return {k: v for k, v in kwargs.items() if k not in skip and not k.startswith("_")}


def is_json_list_output_empty(output: Any) -> bool:
    """Wrapper-side empty check for langmem recall JSON list dumps."""
    if isinstance(output, tuple):
        output = output[0]
    if not isinstance(output, str):
        return False
    try:
        parsed = json.loads(output)
    except json.JSONDecodeError:
        return False
    return isinstance(parsed, list) and len(parsed) == 0


def _fingerprint(tool_name: str, args: dict[str, Any]) -> str:
    return f"{tool_name}:{json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)}"
