from __future__ import annotations

import json
from pathlib import Path

from app.agent.runtime.ports import get_canvas_port

_SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"


def _load_skills() -> str:
    """读取 skills 目录下 Markdown, 拼入 system prompt"""
    parts: list[str] = []
    for path in sorted(_SKILLS_DIR.glob("*.md")):
        parts.append(f"## {path.stem}\n\n{path.read_text(encoding='utf-8').strip()}")
    return "\n\n".join(parts)


def _json_meta(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


async def compose_canvas_system_prompt(*, project_id: int, episode_id: int) -> str:
    """组合项目元信息, 技能文档, 工具调用硬性规则"""
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
"""
    # policy 块进入 LLM 上下文, 代码注释不会
    return f"{skills}\n\n## Project\n\n{meta_block}\n\n{policy}"
