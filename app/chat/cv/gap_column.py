"""Column-projection gap detection for slider puzzles."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def find_gap_x(
    *,
    background_path: Path | None = None,
    piece_path: Path | None = None,
    image_path: Path | None = None,
    top_k: int = 3,
) -> dict[str, Any]:
    try:
        import cv2
        import numpy as np
    except ImportError as exc:
        raise RuntimeError("opencv not available") from exc

    if background_path and piece_path:
        bg = cv2.imread(str(background_path), cv2.IMREAD_GRAYSCALE)
        piece = cv2.imread(str(piece_path), cv2.IMREAD_GRAYSCALE)
        if bg is None or piece is None:
            raise RuntimeError("failed to read background or piece image")
        diff = cv2.absdiff(bg, piece)
        col_score = diff.mean(axis=0)
    elif image_path:
        img = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
        if img is None:
            raise RuntimeError("failed to read image")
        col_score = np.abs(np.diff(img.astype(np.float32), axis=1)).mean(axis=0)
    else:
        raise RuntimeError("provide background_path+piece_path or image_path")

    scores = [(int(i), float(s)) for i, s in enumerate(col_score)]
    scores.sort(key=lambda item: item[1], reverse=True)
    candidates = [{"x": x, "score": score} for x, score in scores[: max(1, top_k)]]
    confidence = candidates[0]["score"] if candidates else 0.0
    return {"candidates": candidates, "confidence": confidence}
