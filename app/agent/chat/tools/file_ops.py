from pathlib import Path

from app.agent.chat.tools.result import FILE_NOT_FOUND, INVALID_ARGUMENTS, ToolResult
from app.agent.chat.workspace import resolve_workspace_file

ALLOWED_WRITE_SUFFIXES = frozenset(
    {
        ".txt",
        ".md",
        ".markdown",
        ".csv",
        ".json",
        ".jsonl",
        ".xml",
        ".html",
        ".htm",
        ".yaml",
        ".yml",
        ".log",
        ".py",
        ".sh",
        ".sql",
        ".tsv",
    }
)

WRITE_FILE_REJECT_DETAIL = "此工具只能写 UTF-8 文本文件，二进制或 Office 格式请用 execute_python 生成"

READ_BINARY_SUFFIXES = frozenset(
    {
        ".docx",
        ".doc",
        ".xlsx",
        ".xls",
        ".pptx",
        ".ppt",
        ".pdf",
        ".zip",
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".bin",
    }
)

READ_BINARY_REJECT_DETAIL = (
    "这是二进制/Office 文件，read_file 无法正确读取。"
    " docx 请用 execute_python 运行 skills/docx/scripts/office/unpack.py 解包后再读 unpacked/ 下的文本。"
)


PATH_ESCAPE_DETAIL = "path must be relative to the workspace root (no absolute paths or .. segments)"


def resolve_workspace_path(workspace: Path, path: str) -> Path:
    return resolve_workspace_file(workspace, path)


def _path_escape_error(path: str) -> ToolResult:
    return ToolResult.fail(
        INVALID_ARGUMENTS,
        detail=f"{PATH_ESCAPE_DETAIL}\npath: {path}",
    )


def try_resolve_workspace_path(workspace: Path, path: str) -> Path | None:
    try:
        return resolve_workspace_file(workspace, path)
    except ValueError:
        return None


def read_workspace_file(workspace: Path, path: str, *, max_chars: int = 20_000) -> ToolResult:
    target = try_resolve_workspace_path(workspace, path)
    if target is None:
        return _path_escape_error(path)
    if not target.exists():
        return ToolResult.fail(FILE_NOT_FOUND, detail=f"file not found: {path}", output=f"file not found: {path}")
    suffix = target.suffix.lower()
    if suffix in READ_BINARY_SUFFIXES:
        return ToolResult.fail(
            INVALID_ARGUMENTS,
            detail=f"{READ_BINARY_REJECT_DETAIL}\npath: {path}",
        )
    text = target.read_text(encoding="utf-8", errors="replace")
    if len(text) > max_chars:
        text = text[:max_chars] + f"\n\n[truncated: 文件共 {len(text)} 字符，仅显示前 {max_chars}]"
    return ToolResult.ok(text)


def write_workspace_file(workspace: Path, path: str, content: str) -> ToolResult:
    suffix = Path(path).suffix.lower()
    if not suffix or suffix not in ALLOWED_WRITE_SUFFIXES:
        return ToolResult.fail(INVALID_ARGUMENTS, detail=WRITE_FILE_REJECT_DETAIL)
    target = try_resolve_workspace_path(workspace, path)
    if target is None:
        return _path_escape_error(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return ToolResult.ok(f"wrote {path} ({len(content)} bytes)")


def list_workspace_files(workspace: Path, path: str = ".") -> ToolResult:
    target = try_resolve_workspace_path(workspace, path)
    if target is None:
        return _path_escape_error(path)
    if not target.exists():
        return ToolResult.fail(FILE_NOT_FOUND, detail=f"not found: {path}", output=f"not found: {path}")
    if target.is_file():
        return ToolResult.ok(path)
    lines = []
    for item in sorted(target.rglob("*")):
        rel = item.relative_to(workspace)
        if item.is_file():
            lines.append(str(rel))
    return ToolResult.ok("\n".join(lines[:200]) or "(empty)")


def is_skills_path(path: str) -> bool:
    normalized = path.replace("\\", "/").lstrip("./")
    return normalized == "skills" or normalized.startswith("skills/")


def is_browser_raw_path(path: str) -> bool:
    normalized = path.replace("\\", "/").lstrip("./")
    return normalized == "raw" or normalized.startswith("raw/")


def is_browser_internal_path(path: str) -> bool:
    normalized = path.replace("\\", "/").lstrip("./")
    return normalized == ".browser" or normalized.startswith(".browser/")


PUBLISH_SKILLS_REJECT_DETAIL = (
    "这是系统内部 skill 文件，不是可交付物；请将 execute_python 生成的输出 publish_file"
)
PUBLISH_BROWSER_RAW_REJECT_DETAIL = (
    "browser/raw 为抓取中间产物目录，请 ETL 清洗后再 publish 最终文件"
)
PUBLISH_BROWSER_INTERNAL_REJECT_DETAIL = (
    ".browser/ 为会话存储目录，不可 publish；请 publish 清洗后的 output 文件"
)
