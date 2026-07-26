from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from detect_secrets.core.scan import scan_line
from detect_secrets.settings import default_settings, transient_settings

# 排除高熵插件，避免普通短句误报为密钥
_EXCLUDED_PLUGIN_NAMES = frozenset(
    {
        "Base64HighEntropyString",
        "HexHighEntropyString",
    }
)


@dataclass(frozen=True)
class SecretScanHit:
    """detect-secrets 命中摘要，不含密文"""

    secret_type: str


def _scan_settings() -> dict[str, Any]:
    """默认插件集去掉两个高熵插件"""
    with default_settings() as settings:
        raw = settings.json()
    plugins = [
        plugin
        for plugin in raw.get("plugins_used", [])
        if plugin.get("name") not in _EXCLUDED_PLUGIN_NAMES
    ]
    return {**raw, "plugins_used": plugins}


def scan_text_for_secrets(text: str) -> list[SecretScanHit]:
    """用 detect-secrets 扫描文本，空列表表示未命中"""
    if not text or not text.strip():
        return []
    hits: list[SecretScanHit] = []
    with transient_settings(_scan_settings()):
        for line in text.splitlines() or [text]:
            for secret in scan_line(line):
                hits.append(SecretScanHit(secret_type=str(secret.type)))
    return hits


def text_has_secrets(text: str) -> bool:
    """文本是否被 detect-secrets 命中"""
    return bool(scan_text_for_secrets(text))
