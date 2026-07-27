from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter


class TurnTextBlock(BaseModel):
    """文本内容块"""

    model_config = ConfigDict(extra="forbid")

    type: Literal["text"] = "text"
    text: str


class TurnSkillBlock(BaseModel):
    """技能引用内容块"""

    model_config = ConfigDict(extra="forbid")

    type: Literal["skill"] = "skill"
    path: str = Field(min_length=1)


TurnContentBlock = Annotated[TurnTextBlock | TurnSkillBlock, Field(discriminator="type")]

_TURN_CONTENT_ADAPTER: TypeAdapter[list[TurnContentBlock]] = TypeAdapter(list[TurnContentBlock])


class TurnUserInput(BaseModel):
    """结构化 turn 用户输入"""

    model_config = ConfigDict(extra="forbid")

    content: list[TurnContentBlock] = Field(min_length=1)
    materials: list[Any] = Field(default_factory=list)


def validate_turn_user_input(input: TurnUserInput) -> TurnUserInput:
    """校验 turn 输入, 技能引用后须含非空文本"""
    has_text = any(
        isinstance(block, TurnTextBlock) and block.text.strip()
        for block in input.content
    )
    if not has_text:
        raise ValueError("content_required_after_skill_refs")
    if input.materials:
        raise ValueError("materials not supported in this phase")
    return input


def extract_skill_paths(content: list[TurnContentBlock]) -> list[str]:
    """从内容块提取去重后的技能路径"""
    seen: set[str] = set()
    paths: list[str] = []
    for block in content:
        if isinstance(block, TurnSkillBlock):
            if block.path not in seen:
                seen.add(block.path)
                paths.append(block.path)
    return paths


def compile_human_text(content: list[TurnContentBlock]) -> str:
    """将内容块编译为 HumanMessage 文本"""
    parts: list[str] = []
    for block in content:
        if isinstance(block, TurnTextBlock):
            parts.append(block.text)
        else:
            parts.append(f"[skill:{block.path}]")
    return "\n".join(parts)


def input_snapshot_dict(input: TurnUserInput) -> dict[str, Any]:
    """生成可持久化的输入快照"""
    return input.model_dump(mode="json")


def parse_turn_content_blocks(raw: list[Any]) -> list[TurnContentBlock]:
    """解析并校验内容块列表"""
    return _TURN_CONTENT_ADAPTER.validate_python(raw)
