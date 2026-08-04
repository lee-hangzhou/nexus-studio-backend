from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.agent.chat.tools.lc_tools import ChatToolContext
from app.agent.runtime.tools.result import ToolResult
from app.agent.workshop import host_tools as host_tools_mod
from app.agent.workshop.host_tools import build_host_orchestration_tools
from app.server.ports.product import (
    WorkshopScheduleDTO,
    WorkshopWorkflowDTO,
    WorkshopWorkflowRunDTO,
)


def _tool_ctx(*, model_key: str = "test-model") -> ChatToolContext:
    return ChatToolContext(
        user_id=1,
        conversation_id=1,
        workspace=Path("/tmp"),
        audit=[],
        model_key=model_key,
    )


def _by_name(tools: list) -> dict[str, Any]:
    return {tool.name: tool for tool in tools}


def _call(name: str, args: dict[str, Any], call_id: str = "tc1") -> dict[str, Any]:
    return {
        "name": name,
        "args": args,
        "id": call_id,
        "type": "tool_call",
    }


def _content(raw: Any) -> str:
    content = getattr(raw, "content", raw)
    assert isinstance(content, str)
    return content


def _output_json(raw: Any) -> dict[str, Any]:
    result = ToolResult.parse_tool_message(_content(raw))
    assert result.success
    payload = json.loads(result.output)
    assert isinstance(payload, dict)
    return payload


@pytest.mark.asyncio
async def test_draft_workflow_requires_model_key() -> None:
    """无回合 model_key 时 draft_workflow fail-closed"""
    tools = _by_name(build_host_orchestration_tools(_tool_ctx(model_key=""), project_id="wp_1"))
    raw = await tools["draft_workflow"].ainvoke(
        _call(
            "draft_workflow",
            {
                "name": "走动提醒",
                "nodes": [
                    {
                        "id": "n1",
                        "title": "提醒",
                        "instruction": "提醒用户起身走动",
                        "preset_key": "ecom_market_competitor_advisor",
                        "output_name": "result",
                    }
                ],
            },
        )
    )
    result = ToolResult.parse_tool_message(_content(raw))
    assert not result.success
    assert result.error_detail == "turn model_key required"


@pytest.mark.asyncio
async def test_draft_confirm_schedule_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """Host 工具链：draft → confirm → schedule / manual run"""
    port = AsyncMock()
    port.agent_draft_workflow = AsyncMock(
        return_value=WorkshopWorkflowDTO(
            id="wf_1",
            name="走动提醒",
            status="draft",
            model_key="test-model",
            revision=1,
        )
    )
    port.confirm_save_workflow = AsyncMock(
        return_value=WorkshopWorkflowDTO(
            id="wf_1",
            name="走动提醒",
            status="saved",
            model_key="test-model",
            revision=1,
        )
    )
    port.create_schedule = AsyncMock(
        return_value=WorkshopScheduleDTO(
            id="sch_1",
            workflow_id="wf_1",
            cron="*/5 * * * *",
            timezone="Asia/Shanghai",
            enabled=True,
        )
    )
    port.manual_run_workflow = AsyncMock(
        return_value=WorkshopWorkflowRunDTO(
            id="run_1",
            workflow_id="wf_1",
            status="queued",
        )
    )
    port.start_workflow_execution = AsyncMock(
        return_value=(
            WorkshopScheduleDTO(
                id="sch_1",
                workflow_id="wf_1",
                cron="*/5 * * * *",
                timezone="Asia/Shanghai",
                enabled=True,
            ),
        )
    )
    port.stop_workflow_execution = AsyncMock(
        return_value=(
            WorkshopScheduleDTO(
                id="sch_1",
                workflow_id="wf_1",
                cron="*/5 * * * *",
                timezone="Asia/Shanghai",
                enabled=False,
            ),
        )
    )
    port.delete_workflow = AsyncMock(return_value=None)
    monkeypatch.setattr(host_tools_mod, "get_workshop_port", lambda: port)

    tools = _by_name(build_host_orchestration_tools(_tool_ctx(), project_id="wp_1"))
    draft = _output_json(
        await tools["draft_workflow"].ainvoke(
            _call(
                "draft_workflow",
                {
                    "name": "走动提醒",
                    "nodes": [
                        {
                            "id": "n1",
                            "title": "提醒",
                            "instruction": "提醒用户起身走动",
                            "preset_key": "ecom_market_competitor_advisor",
                            "output_name": "result",
                        }
                    ],
                },
            )
        )
    )
    assert draft["workflow_id"] == "wf_1"
    assert draft["status"] == "draft"
    port.agent_draft_workflow.assert_awaited_once()
    assert port.agent_draft_workflow.await_args.kwargs["model_key"] == "test-model"

    confirm = _output_json(
        await tools["confirm_save_workflow"].ainvoke(
            _call("confirm_save_workflow", {"workflow_id": "wf_1"}, "tc2")
        )
    )
    assert confirm["status"] == "saved"

    schedule = _output_json(
        await tools["create_schedule"].ainvoke(
            _call(
                "create_schedule",
                {"workflow_id": "wf_1", "cron": "*/5 * * * *"},
                "tc3",
            )
        )
    )
    assert schedule["schedule_id"] == "sch_1"
    assert schedule["cron"] == "*/5 * * * *"

    run = _output_json(
        await tools["manual_run_workflow"].ainvoke(
            _call("manual_run_workflow", {"workflow_id": "wf_1"}, "tc4")
        )
    )
    assert run["run_id"] == "run_1"
    assert run["status"] == "queued"

    started = _output_json(
        await tools["start_workflow_execution"].ainvoke(
            _call("start_workflow_execution", {"workflow_id": "wf_1"}, "tc5")
        )
    )
    assert started["schedules"][0]["enabled"] is True

    stopped = _output_json(
        await tools["stop_workflow_execution"].ainvoke(
            _call("stop_workflow_execution", {"workflow_id": "wf_1"}, "tc6")
        )
    )
    assert stopped["schedules"][0]["enabled"] is False

    deleted = _output_json(
        await tools["delete_workflow"].ainvoke(
            _call("delete_workflow", {"workflow_id": "wf_1"}, "tc7")
        )
    )
    assert deleted["deleted"] is True
    port.delete_workflow.assert_awaited_once()
