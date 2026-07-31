from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from app.agent.workshop.skills.locks import (
    SkillVendorError,
    assert_workshop_skill_locks,
    workshop_skills_root,
)
from app.server.workshop.domain.ecommerce.profiles import (
    ECOM_PRESET_KEYS,
    ECOM_PROFILES,
    allowlist_fingerprint,
    get_ecom_profile,
)
from app.server.workshop.domain.enums import WorkshopToolCapability
from app.server.workshop.domain.presets import ECOM_PRESET_EXPERTS, get_preset


def test_six_ecommerce_profiles_have_distinct_allowlist_fingerprints() -> None:
    """六席电商专家能力白名单指纹互不相同"""
    assert len(ECOM_PRESET_KEYS) == 6
    fingerprints = {
        key: allowlist_fingerprint(get_ecom_profile(key).capability_allowlist)
        for key in ECOM_PRESET_KEYS
    }
    assert len(set(fingerprints.values())) == 6


def test_advisor_profile_excludes_write_mcp_and_generation_submit() -> None:
    """市场顾问不得含店铺写、MCP、Generation submit"""
    profile = get_ecom_profile("ecom_market_competitor_advisor")
    forbidden = {
        WorkshopToolCapability.TAOBAO_STORE_WRITE,
        WorkshopToolCapability.BROWSER_WRITE,
        WorkshopToolCapability.MCP,
        WorkshopToolCapability.GENERATION_SUBMIT,
        WorkshopToolCapability.SANDBOX_EXECUTE,
        WorkshopToolCapability.WRITE_PROJECT_FILES,
        WorkshopToolCapability.CREATE_SCHEDULE,
    }
    assert forbidden.isdisjoint(profile.capability_allowlist)


def test_ads_profile_is_beta_and_has_no_bid_mutation_capability() -> None:
    """广告策略专家为 Beta，且无出价/预算写能力"""
    profile = get_ecom_profile("ecom_ads_strategy_analyzer_executor")
    assert profile.beta is True
    assert WorkshopToolCapability.TAOBAO_STORE_WRITE not in profile.capability_allowlist
    assert WorkshopToolCapability.BROWSER_WRITE not in profile.capability_allowlist
    assert WorkshopToolCapability.MCP not in profile.capability_allowlist


def test_skill_locks_fail_closed_when_valid() -> None:
    """已锁定的 vendored skills 通过 fail-closed 校验"""
    assert_workshop_skill_locks()
    root = workshop_skills_root()
    assert (root / "locks.json").is_file()


def test_skill_lock_rejects_missing_adaptation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """缺 adaptation 记录时 vendoring 校验失败"""
    from app.agent.workshop.skills import locks as locks_mod

    demo_spec = {
        "demo": {
            "commit": "abc",
            "license": "MIT",
            "repo": "x",
            "path": "y",
        }
    }
    monkeypatch.setattr(locks_mod, "workshop_skills_root", lambda: tmp_path)
    monkeypatch.setattr(locks_mod, "SKILL_LOCK_SPEC", demo_spec)
    (tmp_path / "locks.json").write_text(
        '{"skills":{"demo":{"commit":"abc","license":"MIT","repo":"x","path":"y"}}}',
        encoding="utf-8",
    )
    skill_dir = tmp_path / "demo"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# demo\n", encoding="utf-8")
    (skill_dir / "LICENSE").write_text("MIT\n", encoding="utf-8")
    with pytest.raises(SkillVendorError, match="adaptation"):
        locks_mod.assert_workshop_skill_locks(tmp_path)


def test_get_preset_resolves_ecom_keys_only() -> None:
    """产品预置仅含电商业务专家 key"""
    assert get_preset("ecom_market_competitor_advisor").key == "ecom_market_competitor_advisor"
    assert get_preset("ecom_listing_planner_executor").key == "ecom_listing_planner_executor"
    assert len(ECOM_PRESET_EXPERTS) == 6
    assert len(ECOM_PROFILES) == 6
    with pytest.raises(KeyError):
        get_preset("research_advisor")


def test_allowlist_fingerprint_is_stable_sha256() -> None:
    """指纹为能力 value 排序后的 sha256"""
    caps = frozenset(
        {
            WorkshopToolCapability.WEB_SEARCH,
            WorkshopToolCapability.BROWSER_READ,
        }
    )
    expected = hashlib.sha256(
        ",".join(
            sorted(
                [
                    WorkshopToolCapability.WEB_SEARCH.value,
                    WorkshopToolCapability.BROWSER_READ.value,
                ]
            )
        ).encode("utf-8")
    ).hexdigest()
    assert allowlist_fingerprint(caps) == expected
