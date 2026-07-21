"""Deterministic validation for browser script API constraints."""

from __future__ import annotations

import re


_PLAYWRIGHT_SELECTOR_IN_EVAL_RE = re.compile(r":has(?:-text)?\s*\(", re.IGNORECASE)


def browser_script_uses_playwright_selector_in_evaluate(code: str) -> bool:
    if not code or not re.search(r"\.evaluate\s*\(", code, re.IGNORECASE):
        return False
    for match in re.finditer(r"\.evaluate\s*\(", code, re.IGNORECASE):
        snippet = code[match.start() : match.start() + 4000]
        if _PLAYWRIGHT_SELECTOR_IN_EVAL_RE.search(snippet):
            return True
    return False
