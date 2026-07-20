"""Typed contract for browser session driver script execution."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import IntEnum
from typing import Any

from app.chat.tools.result import BROWSER_ERROR, INTERNAL, ToolResult


class BrowserExecExitCode(IntEnum):
    SUCCESS = 0


class BrowserExecParseError(ValueError):
    """Driver /v1/exec payload missing or invalid fields."""


@dataclass(frozen=True)
class BrowserExecResult:
    exit_code: int
    stdout: str
    stderr: str


def parse_driver_exec_payload(payload: Mapping[str, Any]) -> BrowserExecResult:
    if "rc" not in payload:
        raise BrowserExecParseError("driver exec payload missing rc")
    raw_rc = payload["rc"]
    if isinstance(raw_rc, bool) or not isinstance(raw_rc, int):
        raise BrowserExecParseError(f"driver exec rc must be int, got {type(raw_rc).__name__}")

    stdout = payload.get("stdout")
    stderr = payload.get("stderr")
    if stdout is not None and not isinstance(stdout, str):
        raise BrowserExecParseError("driver exec stdout must be str when present")
    if stderr is not None and not isinstance(stderr, str):
        raise BrowserExecParseError("driver exec stderr must be str when present")

    return BrowserExecResult(
        exit_code=raw_rc,
        stdout=stdout if isinstance(stdout, str) else "",
        stderr=stderr if isinstance(stderr, str) else "",
    )


def exec_result_to_tool_result(result: BrowserExecResult) -> ToolResult:
    if result.exit_code == BrowserExecExitCode.SUCCESS:
        return ToolResult.ok(result.stdout)
    detail = (result.stderr or result.stdout).strip()
    if not detail:
        detail = f"exit_code={result.exit_code}"
    return ToolResult.fail(BROWSER_ERROR, detail=detail)


def parse_error_to_tool_result(exc: BrowserExecParseError) -> ToolResult:
    return ToolResult.fail(INTERNAL, detail=str(exc))
