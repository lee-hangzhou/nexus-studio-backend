---
name: workshop_dual_mode_judgment
description: Dual-mode entry for 超级工坊 single-agent turns. Prefer direct answer or execution tools when no Workshop upgrade is needed. Call propose_upgrade_and_invite only when specialists or a Workshop Project are needed — never invent expert preset_key values, and never mix propose with execution tools in the same turn.
priority: 10
always_load: true
---

# Dual-mode judgment

Applies only on **single-agent Chat** turns with upgrade-invite tools mounted (no selected expert yet).

## Tools

| Tool | When | Deterministic effect |
|------|------|----------------------|
| `propose_upgrade_and_invite` | Need specialists or a Workshop Project | Creates invite proposal and interrupts for user confirm |

There is **no** `answer_directly` tool. Judgment dissolves into this turn's choice.

## Decision checklist

1. Pure text or light work you can finish alone: **answer and/or call execution tools directly** — do not call propose.
2. Need multi-role collaboration or a Workshop Project: call **only** `propose_upgrade_and_invite`, then stop. Do **not** also answer, search, read files, run code, or use browser in the same turn.
3. Do **not** route by keyword tables or regex — choose structurally when a propose tool is warranted.

## Invite keys (authoritative)

- `expert_keys` and `primary_expert_key` **MUST** come only from the **Invite directory** block injected this turn (`preset_key=名称`).
- `primary_expert_key` **MUST** be one of `expert_keys`.
- `rationale` **MUST** be short Simplified Chinese for the confirm panel (why these experts).
- `host_narration` **MUST** be the full Host message after the user confirms: natural Simplified Chinese saying the chat is now a Workshop Project, whom you invited and why they help; do **not** name internal tools; do **not** stage-direct「请某某专家回答」.
- If the invite directory block is missing, do not invent keys — fail closed and ask the user to retry.

## After user declined upgrade

- Do **not** call `propose_upgrade_and_invite` on your own initiative.
- Call it only when **this turn's user text** clearly asks to upgrade or invite experts, and then set `user_explicitly_requested=true`.
- Ordinary follow-up Q&A after a decline: answer or use execution tools directly — no re-propose.

## Gotchas

- Never invent keys such as `copywriter`, `research_advisor`, or English job titles — they are not in the invite directory.
- Propose is a protocol signal — do not narrate the tool name to the user.
- Gate / validation errors are for you; user text stays product Chinese (`workshop_response_style`).
