"""QR gate asset verification (size, decode)."""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from app.chat.tools.result import QR_ASSET_INVALID, QR_NOT_DECODABLE, ToolResult
from app.core.config import settings


@dataclass(frozen=True)
class QrVerifyResult:
    width: int
    height: int
    decode_ok: bool


class GateCaptureError(Exception):
    """Raised when gate image capture fails verification."""

    def __init__(self, error_type: str, detail: str) -> None:
        super().__init__(detail)
        self.error_type = error_type
        self.detail = detail

    def to_tool_result(self) -> ToolResult:
        return ToolResult.fail(self.error_type, detail=self.detail)


def _png_dimensions(data: bytes) -> tuple[int, int] | None:
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    width = struct.unpack(">I", data[16:20])[0]
    height = struct.unpack(">I", data[20:24])[0]
    return width, height


def decode_qr_png(data: bytes) -> bool:
    arr = np.frombuffer(data, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_GRAYSCALE)
    if img is None:
        return False
    detector = cv2.QRCodeDetector()
    text, _points, _ = detector.detectAndDecode(img)
    return bool(text)


def verify_png_bytes(data: bytes) -> QrVerifyResult:
    dims = _png_dimensions(data)
    if dims is None:
        raise GateCaptureError(
            QR_ASSET_INVALID,
            json.dumps({"width": 0, "height": 0, "reason": "not_png"}, ensure_ascii=False),
        )
    width, height = dims
    min_w = settings.CHAT_QR_MIN_WIDTH
    min_h = settings.CHAT_QR_MIN_HEIGHT
    if width < min_w or height < min_h:
        raise GateCaptureError(
            QR_ASSET_INVALID,
            json.dumps({"width": width, "height": height, "min_width": min_w, "min_height": min_h}, ensure_ascii=False),
        )
    decode_ok = decode_qr_png(data) if settings.CHAT_QR_REQUIRE_DECODE else True
    if settings.CHAT_QR_REQUIRE_DECODE and not decode_ok:
        raise GateCaptureError(
            QR_NOT_DECODABLE,
            json.dumps({"width": width, "height": height}, ensure_ascii=False),
        )
    return QrVerifyResult(width=width, height=height, decode_ok=decode_ok)


def verify_png_file(path: Path) -> QrVerifyResult:
    return verify_png_bytes(path.read_bytes())


def qr_verify_output(result: QrVerifyResult) -> str:
    return json.dumps(
        {
            "qr_verify": {
                "width": result.width,
                "height": result.height,
                "decode_ok": result.decode_ok,
            }
        },
        ensure_ascii=False,
    )
