from pathlib import Path

from app.server.chat.services.vision import CHAT_IMAGE_UPLOAD_MAX_BYTES, is_image_mime
from app.server.infra.config import settings
from app.server.exceptions.base import AppError
from app.server.exceptions.codes import ErrorCode


def _parse_csv_set(raw: str) -> set[str]:
    return {item.strip().lower() for item in raw.split(",") if item.strip()}


def allowed_upload_constraints() -> tuple[set[str], set[str]]:
    return _parse_csv_set(settings.CHAT_ALLOWED_UPLOAD_EXTS), _parse_csv_set(settings.CHAT_ALLOWED_UPLOAD_MIMES)


def validate_upload_type(filename: str, mime_type: str) -> None:
    allowed_exts, allowed_mimes = allowed_upload_constraints()
    ext = Path(filename).suffix.lower().lstrip(".")
    mime = (mime_type or "").lower()
    if ext not in allowed_exts or mime not in allowed_mimes:
        raise AppError(
            ErrorCode.INVALID_PARAMS,
            f"unsupported file type: .{ext} ({mime})",
        )


def validate_upload_size(*, filename: str, mime_type: str, size: int) -> None:
    if is_image_mime(mime_type) and size > CHAT_IMAGE_UPLOAD_MAX_BYTES:
        raise AppError(
            ErrorCode.INVALID_PARAMS,
            f"image exceeds upload limit: {size} > {CHAT_IMAGE_UPLOAD_MAX_BYTES}",
        )
