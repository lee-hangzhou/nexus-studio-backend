from pathlib import Path

from app.core.config import settings


def conversation_workspace(user_id: int, conversation_id: int) -> Path:
    root = Path(settings.CHAT_WORKSPACE_ROOT)
    path = (root / str(user_id) / str(conversation_id)).resolve()
    root_resolved = root.resolve()
    if not str(path).startswith(str(root_resolved)):
        raise ValueError("invalid workspace path")
    path.mkdir(parents=True, exist_ok=True)
    return path


def resolve_workspace_file(workspace: Path, relative_path: str) -> Path:
    target = (workspace / relative_path).resolve()
    if not str(target).startswith(str(workspace.resolve())):
        raise ValueError("path escapes workspace")
    return target
