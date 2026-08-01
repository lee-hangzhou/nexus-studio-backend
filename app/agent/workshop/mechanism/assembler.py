"""按 surface 组装工坊机制 Skill Index 与 always_load 正文"""

from __future__ import annotations

from enum import StrEnum

from app.agent.workshop.mechanism.registry import WorkshopMechanismSkillRegistry

WORKSHOP_MECHANISM_SKILL_PREAMBLE = """## Skill Index usage

- Workshop mechanism skills encode **system protocol** (judgment, Host orchestration, reply boundary).
  They are not ecommerce playbooks.
- Always-load mechanism skills for this surface are inlined immediately below this usage block.
- When an **Invite directory** block is present, `preset_key` values may come **only** from that block.
  Never invent expert keys.
"""


class WorkshopMechanismSurface(StrEnum):
    """工坊机制 skill 挂载面"""

    CHAT_DUAL_MODE = "chat_dual_mode"
    CHAT_EXPERT = "chat_expert"
    HOST = "host"
    EXPERT = "expert"


_SURFACE_SKILL_NAMES: dict[WorkshopMechanismSurface, frozenset[str]] = {
    WorkshopMechanismSurface.CHAT_DUAL_MODE: frozenset(
        {"workshop_response_style", "workshop_dual_mode_judgment"}
    ),
    WorkshopMechanismSurface.CHAT_EXPERT: frozenset({"workshop_response_style"}),
    WorkshopMechanismSurface.HOST: frozenset(
        {"workshop_response_style", "workshop_host_orchestration"}
    ),
    WorkshopMechanismSurface.EXPERT: frozenset({"workshop_response_style"}),
}


def assemble_workshop_mechanism_skills_block(
    *,
    surface: WorkshopMechanismSurface,
    invite_directory_block: str | None = None,
    declined_upgrade_block: str | None = None,
) -> str:
    """组装本 surface 的机制 Skill Index → preamble → always_load → 确定性附属块"""
    allowed = _SURFACE_SKILL_NAMES[surface]
    all_skills = WorkshopMechanismSkillRegistry.load()
    selected = [skill for skill in all_skills if skill.name in allowed]
    missing = allowed - {skill.name for skill in selected}
    if missing:
        raise RuntimeError(
            f"workshop mechanism skills missing for surface={surface}: {sorted(missing)}"
        )

    parts: list[str] = ["# Workshop mechanism skills", "", "## Skill Index"]
    for skill in sorted(selected, key=lambda item: item.name):
        parts.append(f"- name={skill.name}: {skill.description}")
    parts.append("")
    parts.append(WORKSHOP_MECHANISM_SKILL_PREAMBLE.strip())

    always_load = sorted(
        (skill for skill in selected if skill.always_load),
        key=lambda item: (item.priority, item.name),
    )
    for skill in always_load:
        parts.append(f"## {skill.name}\n\n{skill.body}")

    if invite_directory_block and invite_directory_block.strip():
        parts.append(
            "## Invite directory (authoritative)\n\n" + invite_directory_block.strip()
        )
    if declined_upgrade_block and declined_upgrade_block.strip():
        parts.append(
            "## Upgrade invite status\n\n" + declined_upgrade_block.strip()
        )

    return "\n\n".join(parts)
