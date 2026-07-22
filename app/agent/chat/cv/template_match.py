"""OpenCV template matching primitive."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def match_template(
    *,
    image_path: Path,
    template_path: Path,
    roi: dict[str, int] | None = None,
) -> dict[str, Any]:
    try:
        import cv2
        import numpy as np
    except ImportError as exc:
        raise RuntimeError("opencv not available") from exc

    image = cv2.imread(str(image_path))
    template = cv2.imread(str(template_path))
    if image is None or template is None:
        raise RuntimeError("failed to read image or template")
    if roi:
        x = int(roi.get("x") or 0)
        y = int(roi.get("y") or 0)
        w = int(roi.get("width") or image.shape[1])
        h = int(roi.get("height") or image.shape[0])
        image = image[y : y + h, x : x + w]
    result = cv2.matchTemplate(image, template, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(result)
    return {
        "dx": int(max_loc[0]),
        "dy": int(max_loc[1]),
        "confidence": float(max_val),
    }
