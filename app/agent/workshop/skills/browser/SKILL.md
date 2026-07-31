---
name: browser
description: Workshop ecommerce V1 vendored skill (locked commit local-chat-browser-skill)
---

# browser

Workshop surface skill body for ecommerce expert pack V1.

Upstream: `nexus-studio-backend` @ `local-chat-browser-skill` (`app/agent/chat/skills/browser/SKILL.md`).

## Workshop adaptation

Reuse chat browser skill only for proven API gaps with BROWSER_WRITE.

## Runtime notes

- Loaded only via Workshop Expert Profile `skill_refs`.
- Do not invent business playbooks beyond upstream / official methods.
- MCP is not a V1 hard dependency.
