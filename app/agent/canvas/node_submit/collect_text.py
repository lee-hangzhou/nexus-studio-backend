from __future__ import annotations

from app.agent.canvas.node_submit.types import WorkflowPromptContent


def pick_connected_prompt_input_texts(
    node_id: str,
    nodes: list[dict],
    edges: list[dict],
) -> list[str]:
    """移植 pickConnectedPromptInputTexts。"""
    node_by_id = {str(node["id"]): node for node in nodes}
    texts: list[str] = []
    seen: set[str] = set()

    for edge in edges:
        if str(edge.get("target")) != node_id:
            continue
        data = edge.get("data") or {}
        if data.get("target_port") != "prompt_input" or data.get("source_port") != "output_text":
            continue
        source = node_by_id.get(str(edge.get("source")))
        source_data = (source or {}).get("data") or {}
        text = str(source_data.get("output_text") or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        texts.append(text)

    return texts


def merge_connected_text_into_submit_content(
    content: WorkflowPromptContent | None,
    connected_texts: list[str],
) -> WorkflowPromptContent:
    base = list(content or [])
    seen: set[str] = set()
    for seg in base:
        if seg.get("type") == "text_ref":
            text = str(seg.get("text") or "").strip()
            if text:
                seen.add(text)

    prefix: WorkflowPromptContent = []
    for text in connected_texts:
        if not text or text in seen:
            continue
        seen.add(text)
        prefix.append({"type": "text_ref", "text": text})

    return prefix + base


def content_to_plain_submit_prompt(content: WorkflowPromptContent | None) -> str:
    if not content:
        return ""
    parts: list[str] = []
    for seg in content:
        if seg.get("type") not in ("text", "text_ref"):
            continue
        text = str(seg.get("text") or "").strip()
        if text:
            parts.append(text)
    return "\n\n".join(parts)
