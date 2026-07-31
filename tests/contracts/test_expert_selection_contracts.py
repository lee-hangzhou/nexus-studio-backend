from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.contracts.workshop import (
    ChatSelectedExpertView,
    ClearChatSelectedExpertRequest,
    ExpertDirectoryEntry,
    ExpertDirectoryResponse,
    ExpertTeamDirectoryEntry,
    SetChatSelectedExpertRequest,
    WorkshopRosterExpertView,
    WorkshopTeamSelectRequiresUpgradeView,
    WorkshopTurnTarget,
    WorkshopUpgradeFromTeamRequest,
)


def test_expert_directory_entry_shape() -> None:
    """ExpertDirectoryEntry 公开字段"""
    entry = ExpertDirectoryEntry.model_validate(
        {
            "key": "research_advisor",
            "name": "研究顾问",
            "role_phrase": "调研与洞察",
            "tags": ["研究", "洞察"],
            "scenes": ["研究"],
            "avatar_id": "avatar_research_advisor",
            "kind": "expert",
            "applicable_tasks": ["research"],
        }
    )
    assert entry.kind == "expert"


def test_chat_selected_expert_view_has_no_internal_fields() -> None:
    """ChatSelectedExpertView 不含 token 等内部字段"""
    view = ChatSelectedExpertView.model_validate(
        {
            "conversation_id": 1,
            "expert_key": "research_advisor",
            "name": "研究顾问",
            "avatar_id": "avatar_research_advisor",
        }
    )
    assert view.expert_key == "research_advisor"
    with pytest.raises(ValidationError):
        ChatSelectedExpertView.model_validate(
            {
                "conversation_id": 1,
                "expert_key": "x",
                "name": "x",
                "avatar_id": "a",
                "token": "secret",
            }
        )


def test_roster_view_has_display_fields() -> None:
    """名册视图含 avatar_id、display_name、role_phrase"""
    view = WorkshopRosterExpertView.model_validate(
        {
            "id": "re_1",
            "project_id": "wp_1",
            "name": "研究顾问",
            "kind": "advisor",
            "display_name": "研究顾问",
            "role_phrase": "调研与洞察",
            "avatar_id": "avatar_research_advisor",
        }
    )
    assert view.display_name == "研究顾问"


def test_workshop_turn_target_optional_fields() -> None:
    """WorkshopTurnTarget 可选 expert_id/task_id"""
    target = WorkshopTurnTarget.model_validate({"expert_id": "re_1", "task_id": "t1"})
    assert target.expert_id == "re_1"
    empty = WorkshopTurnTarget.model_validate({})
    assert empty.expert_id is None


def test_team_requires_upgrade_response() -> None:
    """选团队需升级响应契约"""
    view = WorkshopTeamSelectRequiresUpgradeView.model_validate(
        {"requires_upgrade": True, "team_key": "general_collab_team", "message": "需升级"}
    )
    assert view.requires_upgrade is True


def test_upgrade_from_team_request() -> None:
    """WorkshopUpgradeFromTeamRequest 形状"""
    body = WorkshopUpgradeFromTeamRequest.model_validate(
        {"conversation_id": 1, "team_key": "taobao_ops_team"}
    )
    assert body.team_key == "taobao_ops_team"
