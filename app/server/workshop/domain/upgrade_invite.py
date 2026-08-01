from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from app.server.workshop.domain.presets import get_preset


class UpgradeInviteProposalStatus(StrEnum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    DECLINED = "declined"
    SUPERSEDED = "superseded"


class UpgradeInviteProposalError(ValueError):
    """升级邀请提议参数不合法"""


@dataclass(frozen=True, slots=True)
class ValidatedUpgradeInviteProposal:
    """已校验的升级邀请提议"""

    expert_keys: tuple[str, ...]
    primary_expert_key: str
    rationale: str
    host_narration: str


def validate_invite_expert_keys(
    *,
    expert_keys: Sequence[str],
    primary_expert_key: str,
) -> tuple[str, ...]:
    """校验邀请名单与主答；返回去重后的 expert_keys"""
    keys = tuple(dict.fromkeys(key.strip() for key in expert_keys if key and key.strip()))
    if not keys:
        raise UpgradeInviteProposalError("at least one expert_key required")

    primary = primary_expert_key.strip()
    if primary not in keys:
        raise UpgradeInviteProposalError("primary_expert_key must be in expert_keys")

    for key in keys:
        try:
            get_preset(key)
        except KeyError as exc:
            raise UpgradeInviteProposalError(f"unknown expert preset: {key}") from exc
    return keys


def validate_upgrade_invite_proposal(
    *,
    expert_keys: Sequence[str],
    primary_expert_key: str,
    rationale: str,
    host_narration: str,
) -> ValidatedUpgradeInviteProposal:
    """校验建议名单、主答、面板说明与确认后 Host 旁白"""
    cleaned_rationale = rationale.strip()
    if not cleaned_rationale:
        raise UpgradeInviteProposalError("rationale required")
    cleaned_host = host_narration.strip()
    if not cleaned_host:
        raise UpgradeInviteProposalError("host_narration required")

    keys = validate_invite_expert_keys(
        expert_keys=expert_keys,
        primary_expert_key=primary_expert_key,
    )
    return ValidatedUpgradeInviteProposal(
        expert_keys=keys,
        primary_expert_key=primary_expert_key.strip(),
        rationale=cleaned_rationale,
        host_narration=cleaned_host,
    )
