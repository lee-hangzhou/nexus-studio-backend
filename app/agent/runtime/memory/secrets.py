from __future__ import annotations

from dataclasses import dataclass

from detect_secrets.core.scan import scan_line
from detect_secrets.settings import default_settings


@dataclass(frozen=True)
class SecretScanHit:
    """detect-secrets 命中摘要，不含密文"""

    secret_type: str


def scan_text_for_secrets(text: str) -> list[SecretScanHit]:
    """用 detect-secrets 扫描文本，空列表表示未命中"""
    if not text or not text.strip():
        return []
    hits: list[SecretScanHit] = []
    with default_settings():
        for line in text.splitlines() or [text]:
            for secret in scan_line(line):
                hits.append(SecretScanHit(secret_type=str(secret.type)))
    return hits


def text_has_secrets(text: str) -> bool:
    """文本是否被 detect-secrets 命中"""
    return bool(scan_text_for_secrets(text))
