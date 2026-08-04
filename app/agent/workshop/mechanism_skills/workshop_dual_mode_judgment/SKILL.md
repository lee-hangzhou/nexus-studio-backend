---
name: workshop_dual_mode_judgment
description: Dual-mode entry for 超级工坊 single-agent turns. Prefer direct answer or execution tools when no Workshop upgrade is needed. Call propose_upgrade_and_invite when a Workshop Project and/or specialists are needed — expert_keys may be empty for project-only upgrade. Never invent expert preset_key values, and never mix propose with execution tools in the same turn.
priority: 10
always_load: true
---

# Dual-mode judgment

Applies only on **single-agent Chat** turns with upgrade-invite tools mounted (no selected expert yet).

## Tools

| Tool | When | Deterministic effect |
|------|------|----------------------|
| `propose_upgrade_and_invite` | Need a Workshop Project (e.g. schedules) and/or specialists | Creates upgrade proposal and interrupts for user confirm |

There is **no** `answer_directly` tool. Judgment dissolves into this turn's choice.

## Decision checklist

1. Pure text or light work you can finish alone: **answer and/or call execution tools directly** — do not call propose.
2. Need a Workshop Project (定时/异步工作流) and/or multi-role collaboration: call **only** `propose_upgrade_and_invite`, then stop. Do **not** also answer, search, read files, run code, or use browser in the same turn.
3. Upgrading to a project **does not require** inviting experts now — `expert_keys=[]` is valid for project-only upgrade; invite later via Host when needed.
4. Do **not** route by keyword tables or regex — choose structurally when a propose tool is warranted.

## Invite keys (authoritative)

- When non-empty, `expert_keys` and `primary_expert_key` **MUST** come only from the **Invite directory** block injected this turn (`preset_key=名称`).
- When `expert_keys` is non-empty, `primary_expert_key` **MUST** be one of them.
- When `expert_keys` is empty, set `primary_expert_key` to `""`.
- `rationale` **MUST** be short Simplified Chinese for the confirm panel (why upgrade; if experts suggested, why them).
- `host_narration` **MUST** be a **short handoff only** (about 1–2 sentences) after confirm:
  - Say the chat is now a Workshop Project.
  - If experts were chosen, briefly name them; if none, do **not** dwell on inviting later.
  - Do **NOT** ask what the user wants next, list questions (内容/频率/专家), or restate their original ask — the automatic post-confirm continue turn handles that.
  - Do **not** name internal tools; do **not** stage-direct「请某某专家回答」.
- If you need named experts but the invite directory block is missing, do not invent keys — fail closed and ask the user to retry.

## After user declined upgrade

- Do **not** call `propose_upgrade_and_invite` on your own initiative.
- Call it only when **this turn's user text** clearly asks to upgrade or invite experts, and then set `user_explicitly_requested=true`.
- Ordinary follow-up Q&A after a decline: answer or use execution tools directly — no re-propose.

## Gotchas

- Never invent keys such as `copywriter`, `research_advisor`, or English job titles — they are not in the invite directory.
- Propose is a protocol signal — do not narrate the tool name to the user.
- Gate / validation errors are for you; user text stays product Chinese (`workshop_response_style`).
