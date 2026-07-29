"""SKILL.md frontmatter 解析"""

from __future__ import annotations

import re

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---", re.DOTALL)


def parse_skill_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """解析 frontmatter 键值与正文；缺分隔或畸形行则失败"""
    match = _FRONTMATTER_RE.match(text)
    if not match:
        raise RuntimeError("Skill frontmatter missing or malformed")
    meta: dict[str, str] = {}
    for line in match.group(1).splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if ":" not in line:
            raise RuntimeError(f"Skill frontmatter line missing ':': {stripped!r}")
        key, raw = line.split(":", 1)
        value = raw.strip()
        if (value.startswith('"') and value.endswith('"')) or (
            value.startswith("'") and value.endswith("'")
        ):
            value = value[1:-1]
        key_stripped = key.strip()
        if not key_stripped:
            raise RuntimeError(f"Skill frontmatter empty key: {stripped!r}")
        meta[key_stripped] = value
    body = text[match.end() :].lstrip("\n").strip()
    return meta, body
