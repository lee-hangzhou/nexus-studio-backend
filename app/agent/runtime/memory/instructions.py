from __future__ import annotations

from pathlib import Path

USER_MANAGE_INSTRUCTIONS_CHAT = """\
Call manage_user_memory only when:
1. The user explicitly asks to remember, update, or forget something.
2. The user explicitly states a long-lived reply preference that should persist across Chat turns.

Do NOT save background statements such as "I am a programmer" unless the user explicitly asks to remember them.
Do NOT infer occupation, personality, health, intent, or causality from a single turn and write it.
Before update/delete, call recall_user_memory to obtain the memory id; never create a synonym duplicate without id.
Schema fields: statement (one atomic memory), context (optional scope note).
"""

USER_MANAGE_INSTRUCTIONS_CANVAS = """\
Call manage_user_memory only when:
1. The user explicitly asks to remember, update, or forget something for Canvas.
2. The user explicitly states a long-lived reply preference that should persist across Canvas turns.

Do NOT save background statements such as "I am a programmer" unless the user explicitly asks to remember them.
Do NOT infer occupation, personality, health, intent, or causality from a single turn and write it.
Before update/delete, call recall_user_memory to obtain the memory id; never create a synonym duplicate without id.
Schema fields: statement (one atomic memory), context (optional scope note).
This tool is Canvas user scope only; it does not affect Chat user memory.
"""

USER_RECALL_INSTRUCTIONS = """\
Search user-scope long-term memories. Prefer already-injected Memory blocks first.
Call recall when you need a memory id for update/delete, the user asks what you
remember, or injected context is insufficient.
At most once per turn. Empty results mean no matching memories, not a retry signal.
"""

PROJECT_MANAGE_INSTRUCTIONS = """\
Create, update, or delete durable facts for THIS canvas project only.
Use for confirmed project settings, story decisions, and constraints the user stated or confirmed.
Before update/delete, call recall_project_memory for the id.
Do not store credentials, temporary ops, or facts already available from live canvas tools.
"""

PROJECT_RECALL_INSTRUCTIONS = """\
Semantic search of this project's durable memories. Prefer injected Memory (project_facts) first.
Call when you need an id for update/delete or injected project memory is insufficient.
At most once per turn.
"""

FIXED_PROJECT_EXTRACT_INSTRUCTIONS = """\
Extract ONLY durable project facts the user explicitly stated or clearly confirmed in this turn.
Allowed: story settings, character decisions, visual constraints, confirmed project choices.
Forbidden: inferred user profile, causality, hidden intent, assistant self-info, temporary ops,
unconfirmed proposals, and any fact that should be read from live canvas/DB tools.
Prefer update/delete of obsolete project facts over leaving contradictions.
"""

USER_MANAGE_DESCRIPTION_CHAT = (
    "Create, update, or delete one Chat user-scope memory (statement + context).\n\n"
    + USER_MANAGE_INSTRUCTIONS_CHAT
    + "\nReturns structured JSON (success/error_type). "
    "error_type=memory_sensitive_rejected means credentials were rejected. "
    "error_type=memory_unavailable: tell the user briefly memory is unavailable and continue."
)

USER_MANAGE_DESCRIPTION_CANVAS = (
    "Create, update, or delete one Canvas user-scope memory (statement + context).\n\n"
    + USER_MANAGE_INSTRUCTIONS_CANVAS
    + "\nReturns structured JSON (success/error_type). "
    "error_type=memory_sensitive_rejected means credentials were rejected. "
    "error_type=memory_unavailable: tell the user briefly memory is unavailable and continue."
)

USER_RECALL_DESCRIPTION = (
    "Semantic search of user-scope memories.\n\n"
    + USER_RECALL_INSTRUCTIONS
    + "\nReturns JSON list of {id, statement, context}."
)

PROJECT_MANAGE_DESCRIPTION = (
    "Create, update, or delete one project-scope durable fact "
    "(subject/predicate/object/context) for this canvas project.\n\n"
    + PROJECT_MANAGE_INSTRUCTIONS
    + "\nReturns structured JSON (success/error_type)."
)

PROJECT_RECALL_DESCRIPTION = (
    "Semantic search of this canvas project's durable memories.\n\n"
    + PROJECT_RECALL_INSTRUCTIONS
    + "\nReturns JSON list of {id, subject, predicate, object, context}."
)

MEMORY_OPS_BRIEF = (
    Path(__file__).resolve().parents[2] / "chat" / "prompts" / "memory_guide.md"
).read_text(encoding="utf-8").strip()
