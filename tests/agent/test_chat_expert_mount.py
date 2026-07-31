from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agent.chat.expert_turn import (
    build_expert_identity_block,
    intersect_chat_tools_with_profile,
    profile_allowlist_for_key,
    profile_tool_names_for_chat,
)
from app.agent.chat.mount import CHAT_MOUNT, ChatMountContext
from app.contracts.turn_content import TurnUserInput, compile_turn_input
from app.server.workshop.domain.enums import WorkshopToolCapability


def test_profile_tool_names_cannot_gain_taobao_write_from_chat() -> None:
    """Chat 单 Agent 选淘天运营专家仍无 taobao_store_write"""
    names = profile_tool_names_for_chat("ecom_taobao_store_ops_executor")
    assert "taobao_store_write" not in names
    assert "browser_write" not in names
    assert "mcp_invoke" not in names


def test_intersect_chat_tools_filters_unauthorized() -> None:
    """工具列表按 profile ∩ chat 过滤"""
    tool_a = MagicMock()
    tool_a.name = "web_search"
    tool_b = MagicMock()
    tool_b.name = "taobao_store_write"
    allowed = profile_tool_names_for_chat("ecom_market_competitor_advisor")
    filtered = intersect_chat_tools_with_profile([tool_a, tool_b], allowed)
    assert [t.name for t in filtered] == ["web_search"]


def test_build_expert_identity_block_includes_name_and_boundary() -> None:
    """专家身份块含名称与边界"""
    block = build_expert_identity_block("ecom_market_competitor_advisor")
    assert "市场与竞品研究" in block
    assert "只输出有来源的结论" in block


def test_profile_allowlist_ecom_preset_uses_profile_allowlist() -> None:
    """电商 preset 走 profile 能力白名单"""
    allowlist = profile_allowlist_for_key("ecom_market_competitor_advisor")
    assert WorkshopToolCapability.WEB_SEARCH in allowlist
    assert WorkshopToolCapability.TAOBAO_STORE_WRITE not in allowlist


@pytest.mark.asyncio
async def test_prepare_turn_injects_expert_when_selected() -> None:
    """selected_expert_key 进入 prepare_turn 的 prompt 与工具过滤"""
    conversation = MagicMock()
    conversation.selected_expert_key = "ecom_market_competitor_advisor"
    user_input = TurnUserInput(content=[{"type": "text", "text": "hello"}])
    ctx = ChatMountContext(
        user_id=1,
        conversation_id=99,
        turn_id="turn-1",
        cancel_event=__import__("asyncio").Event(),
        checkpointer=MagicMock(),
        conversation=conversation,
        content="hello",
        model_key="test-model",
        enable_tools=True,
        user_input=user_input,
    )
    with patch("app.agent.chat.mount.get_model_spec") as mock_spec:
        mock_spec.return_value = MagicMock()
        with patch("app.agent.chat.mount.ChatAttachments.filter", new_callable=AsyncMock) as mock_att:
            mock_att.return_value = []
            with patch(
                "app.agent.chat.mount.chat_attachment_service.materialize_turn_assets_to_workspace",
                new_callable=AsyncMock,
            ) as mock_mat:
                mock_mat.return_value = []
                with patch("app.agent.chat.mount.persist_user_message", new_callable=AsyncMock):
                    with patch("app.agent.chat.mount.save_turn_observation_snapshot", new_callable=AsyncMock):
                        with patch("app.agent.chat.mount.get_user_skill_port") as mock_skill_port:
                            mock_skill_port.return_value.list_enabled_for_index = AsyncMock(return_value=[])
                            with patch("app.agent.chat.mount.build_memory_injection", new_callable=AsyncMock) as mock_mem:
                                mock_mem.return_value = MagicMock(
                                    memory_blocks_text="",
                                    ops_brief_text=None,
                                )
                                with patch("app.agent.chat.mount.GatewayChatModel") as mock_llm:
                                    mock_llm.return_value = MagicMock()
                                    with patch("app.agent.chat.mount.build_chat_agent") as mock_agent:
                                        mock_agent.return_value = MagicMock()
                                        with patch(
                                            "app.agent.chat.mount.capture_turn_checkpoint_messages",
                                            new_callable=AsyncMock,
                                        ) as mock_cap:
                                            mock_cap.return_value = []
                                            prepared = await CHAT_MOUNT.prepare_turn(ctx)

    assert prepared.agent is not None
    assert "市场与竞品研究" in mock_agent.call_args.kwargs["system_prompt"]
    tool_names = [t.name for t in mock_agent.call_args.args[1]]
    assert "taobao_store_write" not in tool_names
