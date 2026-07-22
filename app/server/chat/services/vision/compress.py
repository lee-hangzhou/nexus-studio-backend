from __future__ import annotations

import base64
import io

from PIL import Image

from app.server.chat.services.vision.types import CHAT_IMAGE_MAX_BASE64_BYTES, IMAGE_COMPRESS_FAILED_MESSAGE
from app.server.exceptions.base import AppError
from app.server.exceptions.codes import ErrorCode


def _base64_len(raw: bytes) -> int:
    return len(base64.standard_b64encode(raw))


def compress_image_bytes(raw: bytes, *, mime_type: str) -> tuple[bytes, str]:
    """按比例缩小分辨率直至 base64 编码后 ≤ CHAT_IMAGE_MAX_BASE64_BYTES。"""
    if _base64_len(raw) <= CHAT_IMAGE_MAX_BASE64_BYTES:
        return raw, mime_type

    image = Image.open(io.BytesIO(raw))
    if image.mode not in ("RGB", "RGBA", "L"):
        image = image.convert("RGB")

    output_mime = mime_type if mime_type in {"image/png", "image/jpeg", "image/webp"} else "image/jpeg"
    fmt = {"image/png": "PNG", "image/jpeg": "JPEG", "image/webp": "WEBP"}.get(output_mime, "JPEG")

    width, height = image.size
    current = image

    for _ in range(24):
        buf = io.BytesIO()
        save_kwargs: dict = {}
        to_save = current
        if fmt == "JPEG" and to_save.mode == "RGBA":
            to_save = to_save.convert("RGB")
        if fmt == "JPEG":
            save_kwargs["quality"] = 85
        to_save.save(buf, format=fmt, **save_kwargs)
        candidate = buf.getvalue()
        if _base64_len(candidate) <= CHAT_IMAGE_MAX_BASE64_BYTES:
            return candidate, output_mime

        width = max(64, int(width * 0.85))
        height = max(64, int(height * 0.85))
        current = current.resize((width, height), Image.Resampling.LANCZOS)

    raise AppError(ErrorCode.INVALID_PARAMS, IMAGE_COMPRESS_FAILED_MESSAGE)
