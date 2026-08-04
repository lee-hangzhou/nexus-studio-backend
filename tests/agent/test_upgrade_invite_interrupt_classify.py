"""升级邀请 interrupt 不得被误判为 PendingToolAction（空 call_id/name 崩前端）"""

from __future__ import annotations

import pytest

from app.agent.runtime.turn_engine.interrupt_model import (
    classify_interrupt_value,
    parse_pending_tool_actions,
)


def _upgrade_invite_payload() -> dict:
    return {
        "upgrade_invite": True,
        "proposal_id": 1,
        "conversation_id": 1305,
        "expert_keys": ["ecom_listing_planner_executor"],
        "primary_expert_key": "ecom_listing_planner_executor",
        "rationale": "需要商品策划与文案",
        "experts": [{"key": "ecom_listing_planner_executor", "name": "商品策划与文案"}],
    }


def _upgrade_invite_payload_empty_experts() -> dict:
    return {
        "upgrade_invite": True,
        "proposal_id": 2,
        "conversation_id": 1306,
        "expert_keys": [],
        "primary_expert_key": "",
        "rationale": "先建工坊项目做定时",
        "experts": [],
    }


def test_upgrade_invite_interrupt_is_not_pending_tool_action() -> None:
    """复现 conv 1305：upgrade_invite 载荷被当成 tool_approval → PendingToolAction 校验失败"""
    classified = classify_interrupt_value(_upgrade_invite_payload())
    assert classified == []


def test_upgrade_invite_empty_experts_still_not_pending_tool_action() -> None:
    classified = classify_interrupt_value(_upgrade_invite_payload_empty_experts())
    assert classified == []


def test_parse_pending_skips_upgrade_invite_without_raising() -> None:
    actions = parse_pending_tool_actions([_upgrade_invite_payload()])
    assert actions == []


def test_upgrade_invite_mixed_with_tool_approval() -> None:
    actions = parse_pending_tool_actions(
        [
            _upgrade_invite_payload(),
            {
                "action_requests": [
                    {"id": "c1", "name": "apply_canvas_patch", "description": "patch"},
                ]
            },
        ]
    )
    assert len(actions) == 1
    assert actions[0].call_id == "c1"


def test_legacy_empty_call_id_fail_closed_not_user_payload() -> None:
    """无 upgrade_invite 标记的空 call_id 仍应 fail closed（不得静默编造）"""
    with pytest.raises(ValueError, match="call_id"):
        classify_interrupt_value({"call_id": "", "name": ""})
