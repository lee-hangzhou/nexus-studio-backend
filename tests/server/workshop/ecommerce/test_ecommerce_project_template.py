from __future__ import annotations

import pytest

from app.server.workshop.domain.ecommerce.profiles import ECOM_PRESET_KEYS
from app.server.workshop.domain.presets import (
    list_invite_directory,
    presets_for_keys,
)
from tests.server.workshop.harness import workshop_harness


def test_presets_for_keys_seeds_ecom_experts() -> None:
    """initial_expert_keys 解析六席电商专家"""
    presets = presets_for_keys(ECOM_PRESET_KEYS)
    assert {p.key for p in presets} == set(ECOM_PRESET_KEYS)
    assert len(presets) == 6


def test_invite_directory_is_product_experts_only() -> None:
    """可邀请目录仅含产品业务专家"""
    directory = list_invite_directory()
    keys = {item.key for item in directory}
    assert keys == set(ECOM_PRESET_KEYS)
    assert len(directory) == 6


@pytest.mark.integration
async def test_create_project_with_ecom_keys_seeds_roster() -> None:
    """initial_expert_keys 初始化六席电商名册"""
    async with workshop_harness() as h:
        project = await h.projects.create_project(
            user_id=h.user_id,
            name="淘天店",
            group_chat_id=h.group_chat_id,
            initial_expert_keys=ECOM_PRESET_KEYS,
        )
        h.track_project(project.id)
        roster = await h.projects.list_roster(
            project_id=project.id, user_id=h.user_id
        )
        preset_keys = {expert.preset_key for expert in roster if expert.preset_key}
        assert preset_keys == set(ECOM_PRESET_KEYS)


@pytest.mark.integration
async def test_create_project_without_keys_has_empty_roster() -> None:
    """无 initial_expert_keys 时名册为空"""
    async with workshop_harness() as h:
        project = await h.projects.create_project(
            user_id=h.user_id,
            name="工坊项目",
            group_chat_id=h.group_chat_id,
        )
        h.track_project(project.id)
        roster = await h.projects.list_roster(
            project_id=project.id, user_id=h.user_id
        )
        assert roster == ()
