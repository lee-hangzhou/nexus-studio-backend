# Chat Skills

Bundled skills live as `{skill_name}/SKILL.md` under this directory. Optional subdirectories:

- `scripts/` — executable helpers invoked via `execute_python`
- additional `.md` files — progressive disclosure sections
- `LICENSE.txt` — license terms for vendored skills

## Frontmatter (required)

Each `SKILL.md` must begin with YAML frontmatter:

```yaml
---
name: docx          # must match directory name
description: "..."  # trigger guidance; copied into system skill index
license: ...        # optional
---
```

Registry parsing fails fast at application startup if:

- a skill directory lacks `SKILL.md`
- frontmatter is missing or malformed
- `name` or `description` is absent
- `name` does not match the directory name

## Adding a new skill

1. Create `app/agent/chat/skills/{name}/SKILL.md` with valid frontmatter.
2. Add scripts and declare sandbox dependencies (LibreOffice, Node, etc.) in skill docs.
3. Restart the application so `SkillRegistry` rescans.
4. Verify `ensure_workspace_session` copies the skill into conversation workspaces.

Root-level files in `app/agent/chat/skills/` (this README, `SOURCES.md`) are **not** copied to workspaces.
