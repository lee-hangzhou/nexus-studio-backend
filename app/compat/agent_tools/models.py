from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class RecoveryParserKind(StrEnum):
    XML_INVOKE = "xml_invoke"
    METHOD_JSON = "method_json"
    OPENAI_JSON = "openai_json"


class RecoveredToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid")

    call_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    args: dict[str, Any] = Field(default_factory=dict)

    def to_langchain_call(self) -> dict[str, Any]:
        return {
            "id": self.call_id,
            "name": self.name,
            "args": self.args,
            "type": "tool_call",
        }


class ToolCallIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    call_id: str
    tool_name: str
    error_code: str
    fields: tuple[str, ...] = ()
    allowed_tools: tuple[str, ...] = ()
    detail: str = ""
    raw_length: int = Field(default=0, ge=0)


@dataclass(frozen=True)
class ToolCallRecoveryResult:
    parser_kind: RecoveryParserKind
    calls: tuple[RecoveredToolCall, ...]
    raw_length: int
    schema_valid: bool
    failure_reason: str | None = None
