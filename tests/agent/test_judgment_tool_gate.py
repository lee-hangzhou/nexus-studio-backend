"""双形态升级邀请门闩：无 answer_directly；propose 后禁止执行工具"""

from __future__ import annotations

from app.agent.chat.tools.judgment_gate import (
    JUDGMENT_TOOL_NAMES,
    PROPOSE_UPGRADE_AND_INVITE,
    UpgradeInviteGateState,
    assert_exec_tool_allowed,
    assert_propose_allowed,
    is_upgrade_protocol_tool,
    mark_propose_started,
)


def test_protocol_tools_recognized() -> None:
    assert is_upgrade_protocol_tool(PROPOSE_UPGRADE_AND_INVITE)
    assert not is_upgrade_protocol_tool("web_search")
    assert not is_upgrade_protocol_tool("answer_directly")
    assert JUDGMENT_TOOL_NAMES == frozenset({PROPOSE_UPGRADE_AND_INVITE})


def test_exec_tools_allowed_before_propose() -> None:
    state = UpgradeInviteGateState(upgrade_invite_declined=False)
    assert_exec_tool_allowed(state, tool_name="web_search")


def test_exec_tools_blocked_after_propose_started() -> None:
    state = UpgradeInviteGateState(upgrade_invite_declined=False)
    mark_propose_started(state)
    try:
        assert_exec_tool_allowed(state, tool_name="web_search")
        raise AssertionError("expected PermissionError")
    except PermissionError as exc:
        assert "propose_upgrade_and_invite" in str(exc) or "升级" in str(exc)


def test_propose_tool_still_allowed_after_started() -> None:
    state = UpgradeInviteGateState(upgrade_invite_declined=False)
    mark_propose_started(state)
    assert_exec_tool_allowed(state, tool_name=PROPOSE_UPGRADE_AND_INVITE)


def test_propose_blocked_after_decline_without_explicit_request() -> None:
    state = UpgradeInviteGateState(upgrade_invite_declined=True)
    try:
        assert_propose_allowed(state, user_explicitly_requested=False)
        raise AssertionError("expected PermissionError")
    except PermissionError as exc:
        assert "拒绝" in str(exc)


def test_propose_allowed_after_decline_when_user_explicitly_requested() -> None:
    state = UpgradeInviteGateState(upgrade_invite_declined=True)
    assert_propose_allowed(state, user_explicitly_requested=True)


def test_propose_allowed_when_not_declined() -> None:
    state = UpgradeInviteGateState(upgrade_invite_declined=False)
    assert_propose_allowed(state, user_explicitly_requested=False)
