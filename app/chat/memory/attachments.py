import io
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import openpyxl
from pypdf import PdfReader

from app.chat.workspace import conversation_workspace
from app.models.chat_attachments import ChatAttachments


def _preview_file_path(user_id: int, conversation_id: int, attachment_id: int) -> Path:
    workspace = conversation_workspace(user_id, conversation_id)
    cache_dir = workspace / ".attachments"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / f"{attachment_id}.preview.txt"


def cache_attachment_preview(
    *,
    user_id: int,
    conversation_id: int,
    attachment_id: int,
    filename: str,
    mime_type: str,
    raw_bytes: bytes,
) -> None:
    """缓存可读摘要，供后续注入 system prompt（不做降级，只做已上传附件摘要）。"""
    preview = _extract_preview(filename=filename, mime_type=mime_type, raw_bytes=raw_bytes)
    path = _preview_file_path(user_id, conversation_id, attachment_id)
    path.write_text(preview, encoding="utf-8")


def _extract_preview(*, filename: str, mime_type: str, raw_bytes: bytes) -> str:
    ext = Path(filename).suffix.lower().lstrip(".")
    if ext in {"txt", "md", "json", "csv"}:
        return raw_bytes.decode("utf-8", errors="replace")

    if ext == "pdf":
        try:
            reader = PdfReader(io.BytesIO(raw_bytes))
            pages = []
            for page in reader.pages:
                pages.append(page.extract_text() or "")
            return "\n".join(pages)
        except Exception:
            return f"[pdf preview unavailable] {filename}"

    if ext == "docx":
        try:
            text = _extract_docx_text(raw_bytes)
            return text if text else f"[docx empty] {filename}"
        except Exception:
            return f"[docx preview unavailable] {filename}"

    if ext == "xlsx":
        try:
            wb = openpyxl.load_workbook(io.BytesIO(raw_bytes), read_only=True, data_only=True)
            lines: list[str] = []
            for ws in wb.worksheets:
                lines.append(f"[sheet] {ws.title}")
                for row in ws.iter_rows(values_only=True):
                    values = [str(v) for v in row if v is not None]
                    if values:
                        lines.append(", ".join(values))
            return "\n".join(lines)
        except Exception:
            return f"[xlsx preview unavailable] {filename}"

    if ext == "pptx":
        return f"[pptx uploaded] {filename}"
    if ext in {"png", "jpg", "jpeg", "webp"}:
        return f"[image uploaded] {filename} ({mime_type})"
    return f"[uploaded file] {filename} ({mime_type})"


def _extract_docx_text(raw_bytes: bytes) -> str:
    """
    从 docx(OpenXML zip) 中提取纯文本：
    - 优先读取 word/document.xml
    - 将段落(<w:p>)分行，文本节点(<w:t>)拼接
    """
    with zipfile.ZipFile(io.BytesIO(raw_bytes)) as zf:
        xml_bytes = zf.read("word/document.xml")

    root = ET.fromstring(xml_bytes)
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    paragraphs: list[str] = []
    for p in root.findall(".//w:p", ns):
        runs = [node.text or "" for node in p.findall(".//w:t", ns)]
        line = "".join(runs)
        if line.strip():
            paragraphs.append(line)
    text = "\n".join(paragraphs)
    # 清理极端控制字符，避免污染 system prompt
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)


async def build_attachment_system_note(attachment_ids: list[int], conversation_id: int, user_id: int) -> str:
    if not attachment_ids:
        return ""
    rows = await ChatAttachments.filter(
        id__in=attachment_ids,
        conversation_id=conversation_id,
        user_id=user_id,
    )
    if not rows:
        return ""
    lines = ["用户附件："]
    for row in rows:
        lines.append(f"- {row.filename} ({row.mime_type}) storage_key={row.storage_key}")
        preview_path = _preview_file_path(user_id, conversation_id, row.id)
        if preview_path.exists():
            preview = preview_path.read_text(encoding="utf-8", errors="replace").strip()
            if preview:
                lines.append(f"  preview:\n{preview}")
    return "\n".join(lines)
