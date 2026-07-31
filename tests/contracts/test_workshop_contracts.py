from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from app.contracts.workshop import (
    ArtifactSubmissionView,
    ScheduleBlockedPayloadView,
    ScheduleStartedPayloadView,
    ScheduleSummaryPayloadView,
    WorkshopCreateProjectRequest,
    WorkshopEventView,
    WorkshopGrantExternalAuthRequest,
    WorkshopProjectView,
    WorkshopRoomMembersResponse,
    WorkshopTaskView,
    WorkshopWorkflowStepView,
)
from app.server.workshop.domain.enums import (
    WorkshopArtifactStorageType,
    WorkshopEventKind,
    WorkshopRole,
    WorkshopTaskStatus,
    WorkshopToolCapability,
)
from scripts.export_contracts import CONTRACTS, SCHEMA_DIR, render_schemas


def test_workshop_contract_forbids_extra_fields() -> None:
    """工坊契约拒绝未知字段"""
    with pytest.raises(ValidationError):
        WorkshopCreateProjectRequest.model_validate(
            {"name": "p", "group_chat_id": 1, "extra": True}
        )
    with pytest.raises(ValidationError):
        WorkshopProjectView.model_validate(
            {
                "id": "wp_1",
                "user_id": 1,
                "name": "p",
                "group_chat_id": 1,
                "pack": "general",
                "host_role": WorkshopRole.HOST,
                "unexpected": 1,
            }
        )


def test_workshop_external_auth_and_capabilities_are_enums() -> None:
    """外部授权与能力字段使用枚举，拒绝裸字符串协议外的值"""
    body = WorkshopGrantExternalAuthRequest.model_validate(
        {
            "project_id": "wp_1",
            "task_id": "t1",
            "capabilities": [WorkshopToolCapability.BROWSER_WRITE.value],
        }
    )
    assert body.capabilities == [WorkshopToolCapability.BROWSER_WRITE]
    with pytest.raises(ValidationError):
        WorkshopGrantExternalAuthRequest.model_validate(
            {
                "project_id": "wp_1",
                "task_id": "t1",
                "capabilities": ["not_a_capability"],
            }
        )


def test_workshop_event_payload_is_typed_not_raw_dict() -> None:
    """事件载荷按 kind 区分类型，禁止 raw dict 协议"""
    started = WorkshopEventView.model_validate(
        {
            "event_key": "evt_1",
            "project_id": "wp_1",
            "task_id": "t1",
            "kind": WorkshopEventKind.SCHEDULE_STARTED,
            "payload": {
                "schedule_id": "sched_1",
                "task_id": "t1",
                "trigger_key": "k1",
                "message": "started",
            },
            "created_at": "2026-01-01T00:00:00+00:00",
        }
    )
    assert isinstance(started.payload, ScheduleStartedPayloadView)
    blocked = WorkshopEventView.model_validate(
        {
            "event_key": "evt_2",
            "project_id": "wp_1",
            "task_id": "t1",
            "kind": WorkshopEventKind.SCHEDULE_BLOCKED,
            "payload": {
                "task_id": "t1",
                "message": "blocked",
                "reasons": ["missing artifact"],
            },
            "created_at": "2026-01-01T00:00:00+00:00",
        }
    )
    assert isinstance(blocked.payload, ScheduleBlockedPayloadView)
    with pytest.raises(ValidationError):
        WorkshopEventView.model_validate(
            {
                "event_key": "evt_3",
                "project_id": "wp_1",
                "task_id": "t1",
                "kind": WorkshopEventKind.SCHEDULE_SUMMARY,
                "payload": {"schedule_id": "sched_1", "task_id": "t1", "trigger_key": "k", "message": "x"},
                "created_at": "2026-01-01T00:00:00+00:00",
            }
        )


def test_workshop_artifact_and_task_views_are_typed() -> None:
    """产物与任务视图保持类型化字段"""
    artifact = ArtifactSubmissionView.model_validate(
        {
            "name": "report",
            "storage_type": WorkshopArtifactStorageType.DB,
            "content": "ok",
        }
    )
    assert artifact.content == "ok"
    task = WorkshopTaskView.model_validate(
        {
            "id": "t1",
            "project_id": "wp_1",
            "title": "goal",
            "goals": ["g1"],
            "required_artifacts": ["report"],
            "status": WorkshopTaskStatus.ALIGNING,
            "schedule_id": None,
            "schedule_authorized": False,
            "external_auth": [],
            "revision": 0,
        }
    )
    assert task.status == WorkshopTaskStatus.ALIGNING
    step = WorkshopWorkflowStepView.model_validate(
        {
            "title": "collect",
            "required_artifact_names": ["a"],
            "external_capabilities": [WorkshopToolCapability.MCP.value],
        }
    )
    assert step.external_capabilities == [WorkshopToolCapability.MCP]


def test_workshop_room_members_response_is_typed() -> None:
    """房间成员响应用显式 expert_ids 列表，禁止裸 dict"""
    view = WorkshopRoomMembersResponse.model_validate({"expert_ids": ["re_1", "re_2"]})
    assert view.expert_ids == ["re_1", "re_2"]
    with pytest.raises(ValidationError):
        WorkshopRoomMembersResponse.model_validate({"items": ["re_1"]})


def test_workshop_contract_export_registered_and_no_drift() -> None:
    """workshop 已注册导出，且生成 schema 与契约无漂移"""
    assert "workshop" in CONTRACTS
    rendered = render_schemas()
    path = SCHEMA_DIR / "workshop.json"
    assert path in rendered
    assert path.exists(), "缺少 contracts/schema/workshop.json，请先 make contracts"
    assert path.read_text(encoding="utf-8") == rendered[path]
    schema = json.loads(path.read_text(encoding="utf-8"))
    assert "$defs" in schema or "anyOf" in schema or "oneOf" in schema
    defs = schema.get("$defs", {})
    for name in (
        "WorkshopConfirmTaskProposalRequest",
        "WorkshopDeclineCustomExpertRequest",
        "WorkshopManualRunWorkflowRequest",
        "WorkshopRecordCapabilityUseRequest",
        "WorkshopScheduleIdRequest",
        "WorkshopScheduleListResponse",
        "WorkshopScheduleRunView",
        "WorkshopCopyPresetRequest",
        "WorkshopCompleteScheduledRunRequest",
    ):
        assert name in defs, f"WorkshopContracts 缺少代表性模型: {name}"


def test_schedule_summary_payload_distinct_from_artifacts() -> None:
    """摘要与产物发布载荷字段集不同，避免互相误解析"""
    summary = ScheduleSummaryPayloadView.model_validate(
        {"task_id": "t1", "message": "done"}
    )
    assert summary.message == "done"
    with pytest.raises(ValidationError):
        ScheduleSummaryPayloadView.model_validate(
            {"task_id": "t1", "artifact_names": ["a"]}
        )
