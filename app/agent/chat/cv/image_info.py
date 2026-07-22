"""Image dimension probe."""

from __future__ import annotations

from pathlib import Path


def image_info(path: Path) -> dict[str, int]:
    try:
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError("PIL not available") from exc
    with Image.open(path) as img:
        channels = len(img.getbands())
        return {"width": img.width, "height": img.height, "channels": channels}
