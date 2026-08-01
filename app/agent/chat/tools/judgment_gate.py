"""单 Agent 升级邀请门闩：propose 互斥执行工具；拒绝后再提议规则"""

from __future__ import annotations

from dataclasses import dataclass

PROPOSE_UPGRADE_AND_INVITE = "propose_upgrade_and_invite"

UPGRADE_PROTOCOL_TOOL_NAMES: frozenset[str] = frozenset({PROPOSE_UPGRADE_AND_INVITE})

# 时间线隐藏用别名，与历史测试/调用点字段名对齐
JUDGMENT_TOOL_NAMES = UPGRADE_PROTOCOL_TOOL_NAMES


@dataclass(slots=True)
class UpgradeInviteGateState:
    """本回合升级邀请门闩状态"""

    upgrade_invite_declined: bool = False
    propose_started: bool = False


def is_upgrade_protocol_tool(tool_name: str) -> bool:
    """是否为升级邀请协议工具"""
    return tool_name in UPGRADE_PROTOCOL_TOOL_NAMES


def mark_propose_started(state: UpgradeInviteGateState) -> None:
    """标记本回合已开始提议升级，此后禁止执行类工具"""
    state.propose_started = True


def assert_exec_tool_allowed(state: UpgradeInviteGateState, *, tool_name: str) -> None:
    """propose 已开始时拒绝非协议执行工具"""
    if is_upgrade_protocol_tool(tool_name):
        return
    if not state.propose_started:
        return
    raise PermissionError(
        "本回合已调用 propose_upgrade_and_invite，禁止再调用执行类工具"
    )


def assert_propose_allowed(
    state: UpgradeInviteGateState, *, user_explicitly_requested: bool
) -> None:
    """拒绝后禁止主动提议；仅本回合用户明确要求时放行"""
    if not state.upgrade_invite_declined:
        return
    if user_explicitly_requested:
        return
    raise PermissionError(
        "用户此前已拒绝升级邀请；仅当本回合用户明确要求升级或邀请专家时，"
        "才可调用并提出 user_explicitly_requested=true"
    )
