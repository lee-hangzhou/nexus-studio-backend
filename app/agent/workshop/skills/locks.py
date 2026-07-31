from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class SkillVendorError(RuntimeError):
    """技能锁定或打包校验失败"""


# Spec pins resolved 2026-07-30
SKILL_LOCK_SPEC: dict[str, dict[str, str]] = {
    "customer-research": {
        "repo": "coreyhaines31/marketingskills",
        "path": "skills/customer-research/SKILL.md",
        "commit": "7868cb9251fad80a73d26e488a5ad5f6c4a9f335",
        "license": "MIT",
    },
    "competitive-brief": {
        "repo": "anthropics/knowledge-work-plugins",
        "path": "marketing/skills/competitive-brief/SKILL.md",
        "commit": "01a9cec39b961236e2d99fa2db9b22b534fa27a9",
        "license": "Apache-2.0",
    },
    "competitor-profiling": {
        "repo": "coreyhaines31/marketingskills",
        "path": "skills/competitor-profiling/SKILL.md",
        "commit": "7868cb9251fad80a73d26e488a5ad5f6c4a9f335",
        "license": "MIT",
    },
    "ecommerce-competitor-analysis": {
        "repo": "nexscope-ai/eCommerce-Skills",
        "path": "ecommerce-competitor-analysis/SKILL.md",
        "commit": "56f3288dd1ba3ae7cae43d369115a915229e510b",
        "license": "MIT",
    },
    "product-description-generator": {
        "repo": "nexscope-ai/eCommerce-Skills",
        "path": "product-description-generator/SKILL.md",
        "commit": "56f3288dd1ba3ae7cae43d369115a915229e510b",
        "license": "MIT",
    },
    "cro": {
        "repo": "coreyhaines31/marketingskills",
        "path": "skills/cro/SKILL.md",
        "commit": "7868cb9251fad80a73d26e488a5ad5f6c4a9f335",
        "license": "MIT",
    },
    "ecom-landing-pages": {
        "repo": "kgelster/awesome-ecom-skills",
        "path": "skills/ecom-landing-pages/SKILL.md",
        "commit": "6d6f1d4e5e0f9ece9e66a3c859d5fbbc99558688",
        "license": "MIT",
    },
    "ad-creative": {
        "repo": "coreyhaines31/marketingskills",
        "path": "skills/ad-creative/SKILL.md",
        "commit": "7868cb9251fad80a73d26e488a5ad5f6c4a9f335",
        "license": "MIT",
    },
    "campaign-plan": {
        "repo": "anthropics/knowledge-work-plugins",
        "path": "marketing/skills/campaign-plan/SKILL.md",
        "commit": "01a9cec39b961236e2d99fa2db9b22b534fa27a9",
        "license": "Apache-2.0",
    },
    "ecommerce-marketing-strategy-builder": {
        "repo": "nexscope-ai/eCommerce-Skills",
        "path": "ecommerce-marketing-strategy-builder/SKILL.md",
        "commit": "56f3288dd1ba3ae7cae43d369115a915229e510b",
        "license": "MIT",
    },
    "email-sequence": {
        "repo": "anthropics/knowledge-work-plugins",
        "path": "marketing/skills/email-sequence/SKILL.md",
        "commit": "01a9cec39b961236e2d99fa2db9b22b534fa27a9",
        "license": "Apache-2.0",
    },
    "ads": {
        "repo": "coreyhaines31/marketingskills",
        "path": "skills/ads/SKILL.md",
        "commit": "7868cb9251fad80a73d26e488a5ad5f6c4a9f335",
        "license": "MIT",
    },
    "ecommerce-ppc-strategy-planner": {
        "repo": "nexscope-ai/eCommerce-Skills",
        "path": "ecommerce-ppc-strategy-planner/SKILL.md",
        "commit": "56f3288dd1ba3ae7cae43d369115a915229e510b",
        "license": "MIT",
    },
    "performance-report": {
        "repo": "anthropics/knowledge-work-plugins",
        "path": "marketing/skills/performance-report/SKILL.md",
        "commit": "01a9cec39b961236e2d99fa2db9b22b534fa27a9",
        "license": "Apache-2.0",
    },
    "warehouse-optimization": {
        "repo": "nexscope-ai/eCommerce-Skills",
        "path": "warehouse-optimization/SKILL.md",
        "commit": "56f3288dd1ba3ae7cae43d369115a915229e510b",
        "license": "MIT",
    },
    "pricing": {
        "repo": "coreyhaines31/marketingskills",
        "path": "skills/pricing/SKILL.md",
        "commit": "7868cb9251fad80a73d26e488a5ad5f6c4a9f335",
        "license": "MIT",
    },
    "taobao-listing-official-rules": {
        "repo": "open.taobao.com",
        "path": "official-schema-title-rules",
        "commit": "doc-ref-2026-07-30",
        "license": "Official-Method",
    },
    "taobao-open-platform-publish": {
        "repo": "open.taobao.com",
        "path": "official-publish-runbook",
        "commit": "doc-ref-2026-07-30",
        "license": "Official-Method",
    },
    "browser": {
        "repo": "nexus-studio-backend",
        "path": "app/agent/chat/skills/browser/SKILL.md",
        "commit": "local-chat-browser-skill",
        "license": "Project",
    },
}


def workshop_skills_root() -> Path:
    """返回 vendored workshop skills 根目录"""
    return Path(__file__).resolve().parent


def assert_workshop_skill_locks(root: Path | None = None) -> None:
    """锁定项、LICENSE、NOTICE/ADAPTATION 或内容 SHA 不匹配时 fail closed"""
    base = root or workshop_skills_root()
    locks_path = base / "locks.json"
    if not locks_path.is_file():
        raise SkillVendorError("locks.json missing")
    payload: dict[str, Any] = json.loads(locks_path.read_text(encoding="utf-8"))
    skills = payload.get("skills")
    if not isinstance(skills, dict) or not skills:
        raise SkillVendorError("locks.json skills empty")
    for skill_id, expected in SKILL_LOCK_SPEC.items():
        entry = skills.get(skill_id)
        if not isinstance(entry, dict):
            raise SkillVendorError(f"missing lock entry: {skill_id}")
        if entry.get("commit") != expected["commit"]:
            raise SkillVendorError(f"SHA mismatch: {skill_id}")
        skill_dir = base / skill_id
        if not (skill_dir / "SKILL.md").is_file():
            raise SkillVendorError(f"SKILL.md missing: {skill_id}")
        if not (skill_dir / "LICENSE").is_file():
            raise SkillVendorError(f"LICENSE missing: {skill_id}")
        adaptation = skill_dir / "ADAPTATION.md"
        if not adaptation.is_file():
            raise SkillVendorError(f"adaptation missing: {skill_id}")
        text = adaptation.read_text(encoding="utf-8")
        if expected["commit"] not in text:
            raise SkillVendorError(f"adaptation SHA missing: {skill_id}")
        if expected["repo"] not in text:
            raise SkillVendorError(f"adaptation repo missing: {skill_id}")
