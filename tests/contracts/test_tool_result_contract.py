import json

import pytest

from app.agent.chat.tools.result import INVALID_ARGUMENTS, ToolResult, ToolResultProtocolError


def test_malformed_tool_outputs_cannot_become_success() -> None:
    malformed = [
        "",
        "plain legacy output",
        "{not-json",
        json.dumps({"tool_result": {"success": "false", "output": "failed"}}),
        json.dumps({"tool_result": {"success": False, "output": "failed"}}),
    ]

    for content in malformed:
        with pytest.raises(ToolResultProtocolError):
            ToolResult.parse_tool_message(content)


def test_structured_failure_remains_failure_after_round_trip() -> None:
    result = ToolResult.fail(INVALID_ARGUMENTS, detail="missing required field")

    parsed = ToolResult.parse_tool_message(result.to_tool_message())

    assert parsed.success is False
    assert parsed.error_type == INVALID_ARGUMENTS
    assert parsed.error_detail == "missing required field"


def test_display_from_message_and_summarize_use_envelope() -> None:
    from app.agent.runtime.tools.result import summarize_tool_result

    envelope = ToolResult.ok('{"op_id": "x", "nodes": []}').to_tool_message()
    assert ToolResult.display_from_message(envelope, limit=20) == '{"op_id": "x", "node'
    assert summarize_tool_result("apply_canvas_patch", envelope, ok=True).startswith('{"op_id"')


def test_sanitize_preview_accepts_envelope() -> None:
    from app.agent.chat.tools.ui_preview import sanitize_tool_step_preview

    envelope = ToolResult.ok('[{"id":"m1"}]').to_tool_message()
    assert sanitize_tool_step_preview("recall_user_memory", envelope, ok=True) == "已找到 1 条相关记忆"


def test_sanitize_preview_hides_canvas_payload() -> None:
    from app.agent.chat.tools.ui_preview import sanitize_tool_step_preview

    payload = '{"matched": 2, "nodes": [{"id": "a", "revision": 1}, {"id": "b", "revision": 2}]}'
    envelope = ToolResult.ok(payload).to_tool_message()
    assert sanitize_tool_step_preview("query_canvas_nodes", envelope, ok=True) == "已读取 2 个节点"
    assert sanitize_tool_step_preview("apply_canvas_patch", envelope, ok=True) == "已更新画布"
    assert sanitize_tool_step_preview("submit_node_generation", envelope, ok=True) == "已提交生成任务"
    assert sanitize_tool_step_preview("read_file", envelope, ok=True) == "已完成"
