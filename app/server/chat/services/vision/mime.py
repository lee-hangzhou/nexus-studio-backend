from __future__ import annotations


def is_image_mime(mime_type: str) -> bool:
    return (mime_type or "").lower().startswith("image/")
