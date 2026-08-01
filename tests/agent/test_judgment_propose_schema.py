"""propose_upgrade_and_invite 必须保留 InjectedToolCallId（勿用会吞掉注入字段的 args_schema）"""

from __future__ import annotations

from app.agent.chat.tools.judgment_tools import build_judgment_tools
from app.agent.chat.tools.lc_tools import ChatToolContext


def test_propose_upgrade_schema_keeps_injected_tool_call_id() -> None:
    ctx = ChatToolContext(
        user_id=1,
        conversation_id="c1",
        workspace="/tmp",
        audit=[],
        guards=None,
        cancel_event=None,
        loop_guard=None,
        source_user_text="hi",
    )
    tools = {tool.name: tool for tool in build_judgment_tools(ctx)}
    propose = tools["propose_upgrade_and_invite"]
    schema = propose.args_schema
    assert schema is not None
    # LangChain 可能用 create_schema_from_function；字段应含 tool_call_id 注入，不应出现在 JSON schema properties 给模型
    model_fields = getattr(schema, "model_fields", None) or getattr(schema, "__fields__", {})
    assert "tool_call_id" in model_fields or "tool_call_id" in str(propose.args)
    # 显式确认未挂载会吞掉注入的 ProposeUpgradeInviteArgs 作为 StructuredTool.args_schema
    assert getattr(schema, "__name__", "") != "ProposeUpgradeInviteArgs"
