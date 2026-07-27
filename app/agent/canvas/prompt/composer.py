from __future__ import annotations

import json
from pathlib import Path

from app.agent.runtime.ports import get_canvas_port, get_user_skill_port
from app.agent.runtime.skills.prompt_format import (
    format_selected_skill_bodies_text,
    format_user_skill_index_text,
)
from app.agent.runtime.tools.user_skill_protocol import WRITE_USER_SKILL_FILE
from app.server.ports.product import SelectedSkillDTO
from app.server.skills.domain.enums import SkillSurface

_SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"


def _load_skills() -> str:
    """读取 skills 目录下 Markdown, 拼入 system prompt"""
    parts: list[str] = []
    for path in sorted(_SKILLS_DIR.glob("*.md")):
        parts.append(f"## {path.stem}\n\n{path.read_text(encoding='utf-8').strip()}")
    return "\n\n".join(parts)


def _json_meta(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


async def compose_canvas_system_prompt(
    *,
    project_id: int,
    episode_id: int,
    user_id: int,
    selected_skills: tuple[SelectedSkillDTO, ...] = (),
    is_resume: bool = False,
    memory_blocks_text: str = "",
    memory_ops_brief: str | None = None,
) -> str:
    """组合项目元信息、技能、记忆块与工具硬性规则"""
    _ = is_resume
    skills = _load_skills()
    context = await get_canvas_port().get_project_prompt_context(project_id, episode_id)
    meta_lines: list[str] = []
    meta_lines.append(f"project_id={project_id}")
    meta_lines.append(f"episode_id={episode_id}")
    if context.episode_no is not None:
        meta_lines.append(f"episode_no={context.episode_no}")
    if context.episode_name is not None:
        meta_lines.append(f"episode_name={context.episode_name}")
    if context.project_name:
        meta_lines.append(f"project_name={context.project_name}")
    if context.tone_constraint:
        meta_lines.append(f"tone_constraint={_json_meta(context.tone_constraint)}")
    if context.style_constraint:
        meta_lines.append(f"style_constraint={_json_meta(context.style_constraint)}")
    if context.config:
        meta_lines.append(f"config={_json_meta(context.config)}")
    meta_block = "\n".join(meta_lines)

    policy = """## Policy

- Canvas layout is not injected here; call query_canvas_nodes when you need facts.
- apply_canvas_patch: node ids only from prior tool JSON.
- Connect only after create_node returns nodes[].id, and use a separate patch.
- Never use UUIDs from skill examples or invented placeholders in connect/source/target.
- list_generate_models accepts one kind per call (image or video).
- In manual mode, write tools pause for user confirm before executing.
- Tool results use structured JSON; read error_type on failure.
- When the user requests canvas changes or generation, call the registered tools; do not only describe steps in prose.
- Never invent tool results, node UUIDs, task_id, or model_id. Wait for real ToolMessage JSON from the server.
- Do not use XML or JSON roleplay (`<function_calls>`, `<function_response>`,
  `{"method":...}`) instead of real tool calls.
- Injected ## Memory blocks are MEMORY (low authority). Live canvas/tool facts and the current user message override memory.
- """ + WRITE_USER_SKILL_FILE + """ creates user-scoped skills for the canvas surface only.
"""
    parts = [skills, f"## Project\n\n{meta_block}", policy]
    selected_paths = {item.path for item in selected_skills}
    index_items = await get_user_skill_port().list_enabled_for_index(
        surface=SkillSurface.CANVAS,
        user_id=user_id,
        project_id=project_id,
    )
    index_text = format_user_skill_index_text(index_items, selected_paths)
    bodies_text = format_selected_skill_bodies_text(selected_skills)
    if index_text:
        parts.append(index_text)
    if bodies_text:
        parts.append(bodies_text)
    if memory_ops_brief:
        parts.append(memory_ops_brief)
    if memory_blocks_text.strip():
        parts.append(memory_blocks_text.strip())
    return "\n\n".join(parts)
