"""Canvas Skill Index 使用说明文案"""

from __future__ import annotations

CANVAS_SKILL_INDEX_PREAMBLE = """## Skill Index usage

- System skill bodies are not inlined by default (except always_load skills below:
  `canvas_response_style`, `canvas_workflow`, and `canvas_turn_references` when this turn
  has Turn References). Always-load system skills are inlined immediately below this usage block.
- Call `read_canvas_skill` with `name=` from the **Skill Index** above (system skills only).
  Do not pass user skill paths to `read_canvas_skill`.
- When a tool description says a system skill is **required reading before** the tool, call
  `read_canvas_skill` for that skill **in this turn before** calling the tool.
  Query/list tools alone do not waive the read. Do not invent protocol from memory.
- User skills (if any) appear in a separate **用户 Skill** section after always-load bodies;
  pinned user skill bodies are inlined there — follow them for the user request.
"""
