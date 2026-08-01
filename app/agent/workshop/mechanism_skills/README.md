# Workshop mechanism skills

System-protocol skills for 超级工坊 (dual-mode judgment, Host orchestration, public reply boundary).

**Not** ecommerce playbooks. Business packs stay under `app/agent/workshop/skills/` and load only via Profile `skill_refs`.

## Layout

```text
mechanism_skills/                 # SKILL.md packs only (no Python loaders)
  {name}/SKILL.md
  README.md
mechanism/                        # registry / assembler / invite directory
  registry.py
  assembler.py
  invite_directory.py
```

## Frontmatter (required)

Same extensions as Canvas system skills:

```yaml
---
name: workshop_response_style
description: "..."   # WHAT + WHEN; single line (parser is not full YAML)
priority: -100
always_load: true
---
```

Registry raises `RuntimeError` if the directory is missing, empty, duplicate, or frontmatter/body invalid.

## Surfaces

| Skill | Chat dual-mode | Chat expert | Host | Expert |
|-------|----------------|-------------|------|--------|
| `workshop_response_style` | always | always | always | always |
| `workshop_dual_mode_judgment` | always | — | — | — |
| `workshop_host_orchestration` | — | — | always | — |

Invite `preset_key` list is injected as a **deterministic** block from `list_invite_directory()` — never hardcode keys in skill bodies.

## Do not

- Put business playbooks here
- Copy these skills into conversation workspaces (unlike Chat bundled file skills)
- Invent invite keys in prompts or tests
