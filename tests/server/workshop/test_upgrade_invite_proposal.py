"""UpgradeInviteProposal 领域校验（公开 seam）"""

from __future__ import annotations

import pytest

from app.server.workshop.domain.upgrade_invite import (
    UpgradeInviteProposalError,
    validate_invite_expert_keys,
    validate_upgrade_invite_proposal,
)


def test_validate_accepts_primary_in_suggested_set() -> None:
    result = validate_upgrade_invite_proposal(
        expert_keys=("ecom_listing_planner_executor", "ecom_market_competitor_advisor"),
        primary_expert_key="ecom_listing_planner_executor",
        rationale="新品上市需要商品策划主答",
        host_narration="已经帮你升级成工坊，并请来商品策划与文案和市场研究两位搭档一起推进。",
    )
    assert result.expert_keys == (
        "ecom_listing_planner_executor",
        "ecom_market_competitor_advisor",
    )
    assert result.primary_expert_key == "ecom_listing_planner_executor"
    assert "商品策划" in result.rationale or result.rationale
    assert "工坊" in result.host_narration


def test_validate_rejects_empty_expert_keys() -> None:
    with pytest.raises(UpgradeInviteProposalError, match="at least one"):
        validate_upgrade_invite_proposal(
            expert_keys=(),
            primary_expert_key="ecom_listing_planner_executor",
            rationale="x",
            host_narration="y",
        )


def test_validate_rejects_primary_outside_set() -> None:
    with pytest.raises(UpgradeInviteProposalError, match="primary"):
        validate_upgrade_invite_proposal(
            expert_keys=("ecom_listing_planner_executor",),
            primary_expert_key="ecom_market_competitor_advisor",
            rationale="x",
            host_narration="y",
        )


def test_validate_rejects_unknown_preset() -> None:
    with pytest.raises(UpgradeInviteProposalError, match="unknown"):
        validate_upgrade_invite_proposal(
            expert_keys=("not_a_real_expert",),
            primary_expert_key="not_a_real_expert",
            rationale="x",
            host_narration="y",
        )


def test_validate_rejects_blank_rationale() -> None:
    with pytest.raises(UpgradeInviteProposalError, match="rationale"):
        validate_upgrade_invite_proposal(
            expert_keys=("ecom_listing_planner_executor",),
            primary_expert_key="ecom_listing_planner_executor",
            rationale="   ",
            host_narration="已经升级为工坊。",
        )


def test_validate_rejects_blank_host_narration() -> None:
    with pytest.raises(UpgradeInviteProposalError, match="host_narration"):
        validate_upgrade_invite_proposal(
            expert_keys=("ecom_listing_planner_executor",),
            primary_expert_key="ecom_listing_planner_executor",
            rationale="需要商品策划",
            host_narration="   ",
        )


def test_validate_invite_expert_keys_alone() -> None:
    keys = validate_invite_expert_keys(
        expert_keys=("ecom_listing_planner_executor",),
        primary_expert_key="ecom_listing_planner_executor",
    )
    assert keys == ("ecom_listing_planner_executor",)
