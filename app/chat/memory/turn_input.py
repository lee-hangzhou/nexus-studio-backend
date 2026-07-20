from __future__ import annotations

from langchain_core.messages import HumanMessage

from app.chat.prompt.types import AttachmentBrief
from app.chat.vision.types import IMAGE_REF_TYPE, TEXT_BLOCK_TYPE

USER_MESSAGE_SECTION_HEADER = "## 用户消息\n"

_WORKSPACE_SCRIPT_PATH = """\
- execute_python 在 workspace 根目录执行（cwd=/workspace），不是 skill 子目录。
- 调用 skill 脚本时必须写完整相对路径，例如：
  `python skills/docx/scripts/office/unpack.py attachments/文件.docx unpacked/`
- 禁止直接使用 SKILL.md 里的 `scripts/...` 短路径。"""

_WORKSPACE_REQUIREMENTS_WITH_ATTACHMENTS = """\
## 工作区操作要求
- 处理文件前，必须先根据文件扩展名找到对应 skill（见 system prompt 中的 skill 索引）。
- read_file 该 skill 的 SKILL.md，再 execute_python 按说明调用 scripts。
- attachments/ 下为用户上传的原始文件，是文件操作的对象路径；不得把 skills/ 下文件作为用户交付物。
- 交付用户可下载的文件必须 publish_file。
- 路径必须在 attachments/ 或你在 workspace 内新生成的输出路径下，不得 publish skills/ 下任何路径。
""" + _WORKSPACE_SCRIPT_PATH

_WORKSPACE_REQUIREMENTS_NO_ATTACHMENTS = """\
## 工作区操作要求
- 处理文件前，必须先根据文件扩展名找到对应 skill（见 system prompt 中的 skill 索引）。
- read_file 该 skill 的 SKILL.md，再 execute_python 按说明调用 scripts。
- 交付用户可下载的文件必须 publish_file。
- 路径必须在 attachments/ 或你在 workspace 内新生成的输出路径下，不得 publish skills/ 下任何路径。
""" + _WORKSPACE_SCRIPT_PATH


def build_attachment_context_block(
    *,
    attachments: list[AttachmentBrief],
    workspace_hint: str,
) -> str:
    lines = ["<turn_context>", "## 本会话附件与工作区"]
    if workspace_hint:
        lines.append(workspace_hint)
    if attachments:
        lines.append("## 附件清单")
        for item in attachments:
            flag = "已挂载" if item.is_attached else "未挂载"
            path = item.workspace_path or f"attachments/{item.filename}"
            lines.append(
                f"- {item.filename} (id={item.attachment_id}, mime={item.mime_type}, "
                f"status={item.status}, path={path}, {flag})"
            )
        lines.append(_WORKSPACE_REQUIREMENTS_WITH_ATTACHMENTS)
    else:
        lines.append("（本会话暂无挂载附件）")
        lines.append(_WORKSPACE_REQUIREMENTS_NO_ATTACHMENTS)
    lines.append("</turn_context>")
    return "\n".join(lines)


def build_turn_human_message(
    user_text: str,
    *,
    attachments: list[AttachmentBrief],
    workspace_hint: str,
    image_attachment_ids: list[int] | None = None,
) -> HumanMessage:
    context = build_attachment_context_block(
        attachments=attachments,
        workspace_hint=workspace_hint,
    )
    body = f"{context}\n\n{USER_MESSAGE_SECTION_HEADER}{user_text}"
    blocks: list[dict] = [{"type": TEXT_BLOCK_TYPE, "text": body}]
    image_ids = image_attachment_ids or []
    brief_by_id = {item.attachment_id: item for item in attachments}
    for attachment_id in image_ids:
        brief = brief_by_id.get(attachment_id)
        if brief is None:
            continue
        blocks.append(
            {
                "type": IMAGE_REF_TYPE,
                "attachment_id": attachment_id,
                "mime_type": brief.mime_type,
            }
        )
    if len(blocks) == 1:
        return HumanMessage(content=body)
    return HumanMessage(content=blocks)
