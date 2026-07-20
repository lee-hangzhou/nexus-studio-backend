from __future__ import annotations

import base64
from pathlib import Path

from app.chat.vision.types import CHAT_IMAGE_MAX_BASE64_BYTES, IMAGE_COMPRESS_FAILED_MESSAGE
from app.exceptions.base import AppError
from app.exceptions.codes import ErrorCode


def encode_base64(raw: bytes) -> str:
    return base64.standard_b64encode(raw).decode("ascii")


def load_attachment_bytes(
    *,
    attachment_id: int,
    relative_path: str,
    workspace_root: Path | None,
) -> tuple[bytes, str]:
    if workspace_root is not None and relative_path:
        target = workspace_root / relative_path
        if target.is_file():
            return target.read_bytes(), _guess_mime(relative_path)
    raise AppError(
        ErrorCode.INTERNAL_ERROR,
        f"attachment bytes not available: id={attachment_id} path={relative_path}",
    )


def _guess_mime(relative_path: str) -> str:
    ext = Path(relative_path).suffix.lower()
    return {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
    }.get(ext, "image/jpeg")


def assert_base64_within_limit(b64: str) -> None:
    if len(b64.encode("ascii")) > CHAT_IMAGE_MAX_BASE64_BYTES:
        raise AppError(ErrorCode.INVALID_PARAMS, IMAGE_COMPRESS_FAILED_MESSAGE)
