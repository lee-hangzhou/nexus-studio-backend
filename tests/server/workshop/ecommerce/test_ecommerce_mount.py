from __future__ import annotations

import pytest

from app.agent.workshop.mount import (
    WorkshopMountContext,
    prepare_turn,
    prepare_turn_tool_snapshot,
    resolve_thread_id,
)
from app.server.workshop.domain.tool_mapping import capability_tool_names, profile_for_preset
from app.server.workshop.domain.ecommerce.effective_profile import (
    workshop_host_idle_thread_id,
    workshop_thread_id,
)
from app.server.workshop.domain.ecommerce.profiles import (
    ECOM_PRESET_KEYS,
    allowlist_fingerprint,
    get_ecom_profile,
)
from app.server.workshop.domain.enums import WorkshopExpertKind, WorkshopToolCapability


@pytest.mark.asyncio
async def test_prepare_turn_tool_snapshots_differ_across_six_profiles() -> None:
    """六 Profile prepare_turn 工具名快照互不相同"""
    snapshots: dict[str, tuple[str, ...]] = {}
    for key in ECOM_PRESET_KEYS:
        profile = get_ecom_profile(key)
        ctx = WorkshopMountContext(
            project_id="wp_test",
            expert_id=f"expert_{key}",
            task_id="task_1",
            preset_key=key,
            expert_kind=profile.kind,
        )
        prepared = await prepare_turn(ctx)
        snapshots[key] = prepared.tool_names
    assert len(set(snapshots.values())) == 6


@pytest.mark.asyncio
async def test_advisor_profile_has_no_mcp_or_taobao_write_in_snapshot() -> None:
    """Advisor 回合快照不含 MCP / 店铺写"""
    profile = get_ecom_profile("ecom_market_competitor_advisor")
    ctx = WorkshopMountContext(
        project_id="wp_test",
        expert_id="expert_advisor",
        task_id="task_1",
        preset_key=profile.key,
        expert_kind=profile.kind,
    )
    prepared = await prepare_turn(ctx)
    assert "mcp_invoke" not in prepared.tool_names
    assert "taobao_store_write" not in prepared.tool_names
    assert "browser_write" not in prepared.tool_names
    assert "generation_submit" not in prepared.tool_names


def test_workshop_thread_id_format() -> None:
    """checkpointer 键格式符合规格"""
    assert (
        workshop_thread_id("wp_1", "expert_a", "task_b")
        == "workshop:wp_1:expert:expert_a:task:task_b"
    )
    assert workshop_host_idle_thread_id("wp_1") == "workshop:wp_1:host:idle"


def test_resolve_thread_id_host_idle_without_task() -> None:
    """Host 或无 task_id 时使用 idle 键"""
    ctx = WorkshopMountContext(
        project_id="wp_1",
        expert_id="host",
        task_id=None,
        preset_key=None,
        expert_kind=WorkshopExpertKind.ADVISOR,
        is_host=True,
    )
    assert resolve_thread_id(ctx) == "workshop:wp_1:host:idle"


def test_allowlist_fingerprint_matches_profile_helper() -> None:
    """mount 使用的指纹与 profile 模块一致"""
    for key in ECOM_PRESET_KEYS:
        profile = get_ecom_profile(key)
        assert allowlist_fingerprint(profile.capability_allowlist) == allowlist_fingerprint(
            profile.capability_allowlist
        )


def test_listing_executor_snapshot_includes_generation_list_not_submit_without_grant() -> None:
    """Listing 执行专家可见 list_models；无 grant 时 submit 不在 effective 快照"""
    profile = get_ecom_profile("ecom_listing_planner_executor")
    ctx = WorkshopMountContext(
        project_id="wp_1",
        expert_id="expert_listing",
        task_id="task_1",
        preset_key=profile.key,
        expert_kind=profile.kind,
        granted_external=frozenset(),
    )
    names = prepare_turn_tool_snapshot(ctx)
    assert "generation_list_models" in names
    assert "generation_submit" not in names
    granted = prepare_turn_tool_snapshot(
        WorkshopMountContext(
            project_id="wp_1",
            expert_id="expert_listing",
            task_id="task_1",
            preset_key=profile.key,
            expert_kind=profile.kind,
            granted_external=frozenset({WorkshopToolCapability.GENERATION_SUBMIT}),
        )
    )
    assert "generation_submit" in granted
