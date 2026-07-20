from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage

from app.canvas.node_execution.generation_guard import claim_node_for_generation
from app.canvas.node_execution.prompt import require_node_kind, resolve_execute_prompt
from app.canvas.services.canvas_service import canvas_service
from app.canvas.services.workflow_dispatch import dispatch_node_terminal
from app.chat.llm import get_adapter
from app.chat.llm.registry import get_model_spec
from app.core.config import settings
from app.core.gateway import gateway_client
from app.core.logger import log_exception, logger
from app.domain.canvas.enums import CanvasNodeKind, CanvasNodeStatus
from app.chat.llm.gateway_chat_model import _openai_chat_response_body
from app.exceptions.base import AppError
from app.exceptions.codes import ErrorCode
from app.models.projects import Projects


def _gateway_error_code(response: dict) -> int | None:
    code = response.get("code")
    if isinstance(code, int) and code != 0:
        return code
    return None


def _extract_completion_text(response: dict, *, adapter) -> str:
    parsed = adapter.parse_response(_openai_chat_response_body(response))
    text = str(parsed.get("content") or "").strip()
    if not text:
        raise AppError(ErrorCode.GATEWAY_PROTOCOL_ERROR, "模型返回空文本")
    return text


async def _build_text_system_prompt(project_id: int) -> str:
    project = await Projects.filter(id=project_id).first()
    parts = ["你是短剧画布文本节点生成助手。根据用户指令生成可直接使用的文本内容，直接输出正文，不要解释过程。"]
    if project is not None:
        if project.tone_constraint:
            parts.append(f"语气约束：{project.tone_constraint}")
        if project.style_constraint:
            parts.append(f"风格约束：{project.style_constraint}")
    return "\n".join(parts)


async def execute_text_node_generation(
    *,
    project_id: int,
    user_id: int,
    node_id: str,
    model_key: str,
    prompt: str | None = None,
    expected_revision: int | None = None,
) -> tuple[int, dict]:
    """同步生成文本并写回 canvas node，返回 revision 与 patch delta。"""
    await require_node_kind(project_id, node_id, CanvasNodeKind.TEXT)
    resolved_prompt = await resolve_execute_prompt(project_id, node_id, prompt_override=prompt)

    rev, node_view = await claim_node_for_generation(project_id, node_id)
    if model_key:
        rev, node_view = await canvas_service.update_node_text_output(
            project_id,
            node_id,
            status=CanvasNodeStatus.RUNNING,
            model_id=model_key,
            error_message="",
            expected_revision=rev,
        )

    spec = get_model_spec(model_key)
    adapter = get_adapter(spec.family)
    messages = [
        SystemMessage(content=await _build_text_system_prompt(project_id)),
        HumanMessage(content=resolved_prompt),
    ]
    payload = adapter.build_params(messages, spec.gateway_model, stream=False)

    try:
        response = await gateway_client.openai_chat_completion(
            payload,
            timeout_sec=settings.CHAT_GATEWAY_TIMEOUT_SEC,
        )
        error_code = _gateway_error_code(response)
        if error_code is not None:
            raise AppError(
                ErrorCode.GATEWAY_PROTOCOL_ERROR,
                str(response.get("message") or "gateway error"),
                {"code": error_code},
            )
        output_text = _extract_completion_text(response, adapter=adapter)
    except AppError as exc:
        rev, node_view = await canvas_service.update_node_text_output(
            project_id,
            node_id,
            status=CanvasNodeStatus.FAILED,
            error_message=str(exc.message),
            expected_revision=rev,
        )
        raise
    except Exception as exc:
        log_exception(
            "canvas.text_generation.llm_failed",
            exc=exc,
            project_id=project_id,
            node_id=node_id,
            model_key=model_key,
        )
        rev, node_view = await canvas_service.update_node_text_output(
            project_id,
            node_id,
            status=CanvasNodeStatus.FAILED,
            error_message=str(exc),
            expected_revision=rev,
        )
        raise AppError(ErrorCode.GATEWAY_PROTOCOL_ERROR, "文本生成失败", {"node_id": node_id}) from exc

    rev, node_view = await canvas_service.update_node_text_output(
        project_id,
        node_id,
        status=CanvasNodeStatus.SUCCESS,
        output_text=output_text,
        error_message="",
        expected_revision=rev,
    )
    await dispatch_node_terminal(
        project_id,
        node_id,
        user_id=user_id,
        status=CanvasNodeStatus.SUCCESS,
    )
    logger.info(
        "canvas.text_generation.ok",
        project_id=project_id,
        node_id=node_id,
        model_key=model_key,
        output_chars=len(output_text),
    )
    delta = {
        "revision": rev,
        "nodes": [node_view.model_dump(mode="json")],
        "edges": [],
    }
    return rev, delta
