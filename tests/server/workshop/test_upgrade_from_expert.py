from __future__ import annotations

import pytest

from app.server.workshop.domain.presets import get_preset
from tests.server.workshop.harness import workshop_harness


@pytest.mark.integration
async def test_upgrade_from_expert_seeds_only_selected_roster_and_room() -> None:
    """邀请单专家升级：名册仅该专家，并立即进入房间"""
    async with workshop_harness() as h:
        preset = get_preset("ecom_listing_planner_executor")
        result = await h.projects.confirm_upgrade_from_expert(
            user_id=h.user_id,
            group_chat_id=h.group_chat_id,
            project_name="新品文案",
            carried_message_count=2,
            expert_key=preset.key,
        )
        h.track_project(result.project.id)
        assert result.pending_task_proposal is None
        roster = await h.projects.list_roster(
            project_id=result.project.id, user_id=h.user_id
        )
        room = await h.projects.room_members(
            project_id=result.project.id, user_id=h.user_id
        )
        assert len(roster) == 1
        assert roster[0].preset_key == preset.key
        assert room == {roster[0].id}
        proposals = await h.repository.list_pending_task_proposals(
            project_id=result.project.id, user_id=h.user_id
        )
        assert proposals == []
