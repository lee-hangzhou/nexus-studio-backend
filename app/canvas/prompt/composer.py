from __future__ import annotations

from pathlib import Path

from app.models.projects import Projects

_SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"


def _load_skills() -> str:
    """读取 skills 目录下 Markdown, 拼入 system prompt"""
    parts: list[str] = []
    for path in sorted(_SKILLS_DIR.glob("*.md")):
        parts.append(f"## {path.stem}\n\n{path.read_text(encoding='utf-8').strip()}")
    return "\n\n".join(parts)


async def compose_canvas_system_prompt(*, project_id: int) -> str:
    """组合项目元信息, 技能文档, 工具调用硬性规则"""
    skills = _load_skills()
    project = await Projects.filter(id=project_id).first()
    meta_lines: list[str] = []
    if project is not None:
        meta_lines.append(f"project_id={project_id}")
        if project.name:
            meta_lines.append(f"name={project.name}")
        if project.tone_constraint:
            meta_lines.append(f"tone_constraint={project.tone_constraint}")
        if project.style_constraint:
            meta_lines.append(f"style_constraint={project.style_constraint}")
        if project.config:
            meta_lines.append(f"config={project.config}")
    meta_block = "\n".join(meta_lines) if meta_lines else f"project_id={project_id}"

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
