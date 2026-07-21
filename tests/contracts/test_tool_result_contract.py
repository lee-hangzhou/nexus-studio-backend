import json

import pytest

from app.chat.tools.result import INVALID_ARGUMENTS, ToolResult, ToolResultProtocolError


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
