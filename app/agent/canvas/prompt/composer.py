from __future__ import annotations

import json

from app.agent.runtime.ports import get_canvas_port, get_user_skill_port
from app.agent.runtime.skills.assembler import assemble_canvas_skills_block
from app.agent.runtime.skills.library import CanvasSkillLibrary
from app.agent.runtime.tools.user_skill_protocol import WRITE_USER_SKILL_FILE
from app.contracts.turn_content import TurnReferenceIndex, format_turn_references_block
from app.server.ports.product import SelectedSkillDTO
from app.server.skills.domain.enums import SkillSurface


def _json_meta(value: dict) -> str:
    """将约束字典序列化为稳定 JSON 文本"""
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


async def compose_canvas_system_prompt(
    *,
    project_id: int,
    episode_id: int,
    user_id: int,
    reference_index: TurnReferenceIndex,
    skill_library: CanvasSkillLibrary,
    selected_skills: tuple[SelectedSkillDTO, ...] = (),
    is_resume: bool = False,
    memory_blocks_text: str = "",
    memory_ops_brief: str | None = None,
) -> str:
    """组合 Skill Index、项目元信息、记忆块、Turn References 与工具硬性规则"""
    _ = is_resume
    index_items = await get_user_skill_port().list_enabled_for_index(
        surface=SkillSurface.CANVAS,
        user_id=user_id,
        project_id=project_id,
    )
    skills_block = assemble_canvas_skills_block(
        skill_library=skill_library,
        user_index_items=index_items,
        selected_skills=selected_skills,
    )
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
- apply_canvas_patch: node ids only from prior tool JSON; update_node requires revision from query.
- Connect only after create_node returns nodes[].id, via apply_canvas_edge_operation (separate call).
- Never use UUIDs from skill examples or invented placeholders in connect/source/target.
- list_generate_models accepts one kind per call (image, video, or audio).
- In manual mode, apply_canvas_patch and submit_node_generation pause for user confirm; confirm-card edits win.
- When the user requests canvas changes or generation, call the registered tools; do not only describe steps in prose.
- Never invent tool results, node UUIDs, task_id, or model_id. Wait for real ToolMessage JSON from the server.
- Do not use XML or JSON roleplay (`<function_calls>`, `<function_response>`,
  `{"method":...}`) instead of real tool calls.
- Tool results use structured JSON; read error_type on failure.
- Injected ## Memory blocks are MEMORY (low authority). Live canvas/tool facts and the current user message override memory.
- """ + WRITE_USER_SKILL_FILE + """ creates user-scoped skills for the canvas surface only.
"""
    parts = [skills_block, f"## Project\n\n{meta_block}", policy]
    refs_block = format_turn_references_block(reference_index)
    if refs_block:
        parts.append(refs_block)
    if memory_ops_brief:
        parts.append(memory_ops_brief)
    if memory_blocks_text.strip():
        parts.append(memory_blocks_text.strip())
    return "\n\n".join(parts)
