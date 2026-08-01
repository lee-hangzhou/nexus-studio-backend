"""工坊机制 skill 注册、按 surface 组装与 invite 目录注入"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.agent.workshop.mechanism.assembler import (
    WorkshopMechanismSurface,
    assemble_workshop_mechanism_skills_block,
)
from app.agent.workshop.mechanism.invite_directory import format_invite_directory_prompt
from app.agent.workshop.mechanism.registry import WorkshopMechanismSkillRegistry


def test_workshop_mechanism_registry_loads_mvp() -> None:
    WorkshopMechanismSkillRegistry.reset()
    skills = WorkshopMechanismSkillRegistry.load()
    names = {s.name for s in skills}
    assert names == {
        "workshop_dual_mode_judgment",
        "workshop_host_orchestration",
        "workshop_response_style",
    }
    by_name = {s.name: s for s in skills}
    assert by_name["workshop_response_style"].always_load is True
    assert by_name["workshop_dual_mode_judgment"].always_load is True
    assert by_name["workshop_host_orchestration"].always_load is True
    WorkshopMechanismSkillRegistry.reset()


def test_workshop_mechanism_registry_fail_fast_missing_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    WorkshopMechanismSkillRegistry.reset()
    monkeypatch.setattr(
        WorkshopMechanismSkillRegistry,
        "skills_dir",
        classmethod(lambda cls: tmp_path / "missing"),
    )
    with pytest.raises(RuntimeError, match="mechanism skills directory missing"):
        WorkshopMechanismSkillRegistry.load()
    WorkshopMechanismSkillRegistry.reset()


def test_assemble_chat_dual_mode_includes_judgment_and_invite_keys() -> None:
    WorkshopMechanismSkillRegistry.reset()
    block = assemble_workshop_mechanism_skills_block(
        surface=WorkshopMechanismSurface.CHAT_DUAL_MODE,
        invite_directory_block=format_invite_directory_prompt(),
    )
    assert "## Skill Index" in block
    assert "workshop_response_style" in block
    assert "workshop_dual_mode_judgment" in block
    assert "## workshop_host_orchestration" not in block
    assert "ecom_listing_planner_executor" in block
    assert "ecom_market_competitor_advisor" in block
    invite_section = block.split("## Invite directory (authoritative)", 1)[1]
    assert "ecom_" in invite_section
    assert "copywriter=" not in invite_section
    assert "research_advisor=" not in invite_section
    WorkshopMechanismSkillRegistry.reset()


def test_assemble_host_includes_orchestration_not_dual_mode() -> None:
    WorkshopMechanismSkillRegistry.reset()
    block = assemble_workshop_mechanism_skills_block(
        surface=WorkshopMechanismSurface.HOST,
        invite_directory_block=format_invite_directory_prompt(),
    )
    assert "## workshop_host_orchestration" in block
    assert "## workshop_dual_mode_judgment" not in block
    assert "## workshop_response_style" in block
    assert "invite_experts" in block
    assert "ecom_ops_analytics_executor" in block
    WorkshopMechanismSkillRegistry.reset()


def test_assemble_declined_status_appended() -> None:
    WorkshopMechanismSkillRegistry.reset()
    block = assemble_workshop_mechanism_skills_block(
        surface=WorkshopMechanismSurface.CHAT_DUAL_MODE,
        invite_directory_block=format_invite_directory_prompt(),
        declined_upgrade_block=(
            "用户此前已拒绝升级邀请：禁止主动调用 propose_upgrade_and_invite；"
            "仅当本回合用户明确要求升级或邀请专家时才可调用，"
            "且必须传 user_explicitly_requested=true"
        ),
    )
    assert "user_explicitly_requested=true" in block
    assert "## Upgrade invite status" in block
    WorkshopMechanismSkillRegistry.reset()


def test_format_invite_directory_prompt_lists_real_keys() -> None:
    text = format_invite_directory_prompt()
    assert "preset_key" in text
    assert "ecom_campaign_planner_executor" in text
    assert "ecom_taobao_store_ops_executor" in text
