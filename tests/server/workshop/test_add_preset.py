from __future__ import annotations

import pytest

from tests.server.workshop.harness import workshop_harness


@pytest.mark.asyncio
async def test_add_preset_to_roster_preserves_preset_key() -> None:
    """目录专家加入名册后保留 preset_key，且不自动进房"""
    async with workshop_harness() as h:
        project = await h.projects.create_project(
            user_id=h.user_id,
            name="add-preset",
            group_chat_id=h.group_chat_id,
            initial_expert_keys=(),
        )
        h.track_project(project.id)
        expert = await h.projects.add_preset_to_roster(
            project_id=project.id,
            user_id=h.user_id,
            preset_key="ecom_listing_planner_executor",
        )
        assert expert.preset_key == "ecom_listing_planner_executor"
        room = await h.projects.room_members(project_id=project.id, user_id=h.user_id)
        assert expert.id not in room
        again = await h.projects.add_preset_to_roster(
            project_id=project.id,
            user_id=h.user_id,
            preset_key="ecom_listing_planner_executor",
        )
        assert again.id == expert.id
        assert again.preset_key == "ecom_listing_planner_executor"
