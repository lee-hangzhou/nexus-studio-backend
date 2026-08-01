from __future__ import annotations

import time
from typing import Annotated

from langchain_core.tools import InjectedToolCallId, StructuredTool
from langgraph.types import interrupt
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.agent.chat.tools.judgment_gate import (
    PROPOSE_UPGRADE_AND_INVITE,
    UpgradeInviteGateState,
    assert_exec_tool_allowed,
    assert_propose_allowed,
    is_upgrade_protocol_tool,
    mark_propose_started,
)
from app.agent.chat.tools.lc_tools import ChatToolContext, _emit
from app.agent.chat.tools.result import INVALID_ARGUMENTS, ToolResult
from app.agent.chat.turn.upgrade_invite_emit import (
    UpgradeInviteExpertItem,
    UpgradeInviteInterrupt,
)
from app.server.workshop.domain.presets import get_preset


class ProposeUpgradeInviteArgs(BaseModel):
    """propose_upgrade_and_invite 参数（不含注入字段）"""

    model_config = ConfigDict(extra="forbid")

    expert_keys: list[str] = Field(min_length=1)
    primary_expert_key: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    host_narration: str = Field(min_length=1)
    user_explicitly_requested: bool = False


def build_judgment_tools(ctx: ChatToolContext) -> list[StructuredTool]:
    """组装未升级单 Agent 的升级邀请协议工具"""

    async def _propose_upgrade_and_invite(
        expert_keys: list[str],
        primary_expert_key: str,
        rationale: str,
        host_narration: str,
        user_explicitly_requested: bool = False,
        *,
        tool_call_id: Annotated[str, InjectedToolCallId],
    ) -> str:
        """提议升级工坊并邀请专家，中断回合等待确认"""
        del tool_call_id
        started = time.perf_counter()
        try:
            parsed = ProposeUpgradeInviteArgs(
                expert_keys=expert_keys,
                primary_expert_key=primary_expert_key,
                rationale=rationale,
                host_narration=host_narration,
                user_explicitly_requested=user_explicitly_requested,
            )
        except ValidationError as exc:
            return _emit(
                ctx,
                PROPOSE_UPGRADE_AND_INVITE,
                {
                    "expert_keys": expert_keys,
                    "primary_expert_key": primary_expert_key,
                    "rationale": rationale,
                    "host_narration": host_narration,
                    "user_explicitly_requested": user_explicitly_requested,
                },
                ToolResult.fail(INVALID_ARGUMENTS, detail=str(exc)),
                started,
            )
        args = {
            "expert_keys": parsed.expert_keys,
            "primary_expert_key": parsed.primary_expert_key,
            "rationale": parsed.rationale,
            "host_narration": parsed.host_narration,
            "user_explicitly_requested": parsed.user_explicitly_requested,
        }
        gate = ctx.judgment_gate
        if gate is None:
            return _emit(
                ctx,
                PROPOSE_UPGRADE_AND_INVITE,
                args,
                ToolResult.fail(INVALID_ARGUMENTS, detail="upgrade invite gate not configured"),
                started,
            )
        try:
            assert_propose_allowed(
                gate, user_explicitly_requested=parsed.user_explicitly_requested
            )
        except PermissionError as exc:
            return _emit(
                ctx,
                PROPOSE_UPGRADE_AND_INVITE,
                args,
                ToolResult.fail(INVALID_ARGUMENTS, detail=str(exc)),
                started,
            )
        # 先于 create/await 上锁，压制同轮并行执行工具
        mark_propose_started(gate)
        create = ctx.create_upgrade_invite
        if create is None:
            return _emit(
                ctx,
                PROPOSE_UPGRADE_AND_INVITE,
                args,
                ToolResult.fail(
                    INVALID_ARGUMENTS, detail="upgrade invite callback not configured"
                ),
                started,
            )
        try:
            proposal = await create(
                expert_keys=tuple(parsed.expert_keys),
                primary_expert_key=parsed.primary_expert_key,
                rationale=parsed.rationale,
                host_narration=parsed.host_narration,
            )
        except ValueError as exc:
            return _emit(
                ctx,
                PROPOSE_UPGRADE_AND_INVITE,
                args,
                ToolResult.fail(INVALID_ARGUMENTS, detail=str(exc)),
                started,
            )

        payload = UpgradeInviteInterrupt(
            proposal_id=proposal.id,
            conversation_id=proposal.conversation_id,
            expert_keys=list(proposal.expert_keys),
            primary_expert_key=proposal.primary_expert_key,
            rationale=proposal.rationale,
            experts=[
                UpgradeInviteExpertItem(key=key, name=get_preset(key).name)
                for key in proposal.expert_keys
            ],
        )
        # 确认走 HTTP 新回合，不 resume 本 interrupt
        interrupt(payload.model_dump(mode="python"))
        return _emit(
            ctx,
            PROPOSE_UPGRADE_AND_INVITE,
            args,
            ToolResult.ok(payload.model_dump_json()),
            started,
        )

    return [
        StructuredTool.from_function(
            coroutine=_propose_upgrade_and_invite,
            name=PROPOSE_UPGRADE_AND_INVITE,
            description=(
                "Call only when specialists or a Workshop Project are needed this turn. "
                "Do not call execution tools in the same turn. "
                "expert_keys must be invite-directory preset_key values (at least one); "
                "primary_expert_key must be in expert_keys; "
                "rationale is short Simplified Chinese for the confirm panel; "
                "host_narration is the full Host message shown after the user confirms "
                "(Simplified Chinese, natural speech: that the chat is now a Workshop Project, "
                "whom you invited and why; do not name internal tools; do not stage-direct "
                "「请某某回答」); "
                "after a prior decline, only call when the user explicitly asks this turn and set "
                "user_explicitly_requested=true. Never invent keys. "
                "Interrupts the turn for user confirm/decline. Protocol only — do not narrate to the user."
            ),
        ),
    ]


def wrap_tools_with_judgment_gate(
    ctx: ChatToolContext, tools: list[StructuredTool]
) -> list[StructuredTool]:
    """给非协议工具包上 propose 互斥门闩"""
    gate = ctx.judgment_gate
    if gate is None:
        return list(tools)
    return [
        tool if is_upgrade_protocol_tool(tool.name) else _wrap_one(gate, tool)
        for tool in tools
    ]


def _wrap_one(gate: UpgradeInviteGateState, tool: StructuredTool) -> StructuredTool:
    """包装单个工具的门闩检查"""
    name = tool.name
    description = tool.description
    if not description:
        raise ValueError(f"tool description required: {name}")

    if tool.coroutine is not None:
        original_coro = tool.coroutine

        async def guarded_coro(**kwargs: object) -> object:
            """异步工具门闩包装"""
            try:
                assert_exec_tool_allowed(gate, tool_name=name)
            except PermissionError as exc:
                return ToolResult.fail(INVALID_ARGUMENTS, detail=str(exc)).to_tool_message()
            return await original_coro(**kwargs)

        return StructuredTool.from_function(
            coroutine=guarded_coro,
            name=name,
            description=description,
            args_schema=tool.args_schema,
        )

    original_fn = tool.func
    if original_fn is None:
        raise ValueError(f"tool has neither coroutine nor func: {name}")

    def guarded_fn(**kwargs: object) -> object:
        """同步工具门闩包装"""
        try:
            assert_exec_tool_allowed(gate, tool_name=name)
        except PermissionError as exc:
            return ToolResult.fail(INVALID_ARGUMENTS, detail=str(exc)).to_tool_message()
        return original_fn(**kwargs)

    return StructuredTool.from_function(
        func=guarded_fn,
        name=name,
        description=description,
        args_schema=tool.args_schema,
    )
