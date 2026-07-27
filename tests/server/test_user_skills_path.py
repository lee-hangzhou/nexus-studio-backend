from __future__ import annotations

import pytest

from app.contracts.turn_content import (
    TurnSkillBlock,
    TurnTextBlock,
    TurnUserInput,
    compile_human_text,
    extract_skill_paths,
    validate_turn_user_input,
)
from app.server.exceptions.base import AppError
from app.server.skills.domain.path import normalize_skill_path


def test_normalize_skill_path_valid() -> None:
    assert normalize_skill_path("foo/bar") == "foo/bar"


def test_normalize_skill_path_rejects_empty() -> None:
    with pytest.raises(AppError):
        normalize_skill_path("")


def test_normalize_skill_path_rejects_leading_slash() -> None:
    with pytest.raises(AppError):
        normalize_skill_path("/foo")


def test_validate_turn_user_input_requires_text() -> None:
    with pytest.raises(ValueError, match="content_required_after_skill_refs"):
        validate_turn_user_input(
            TurnUserInput(content=[TurnSkillBlock(path="demo/skill")], materials=[])
        )


def test_validate_turn_user_input_accepts_text_after_skill() -> None:
    result = validate_turn_user_input(
        TurnUserInput(
            content=[
                TurnSkillBlock(path="demo/skill"),
                TurnTextBlock(text="  hello  "),
            ],
            materials=[],
        )
    )
    assert len(result.content) == 2


def test_extract_skill_paths_dedupes() -> None:
    content = [
        TurnSkillBlock(path="a/b"),
        TurnSkillBlock(path="a/b"),
        TurnTextBlock(text="hi"),
    ]
    assert extract_skill_paths(content) == ["a/b"]


def test_compile_human_text_mixed_blocks() -> None:
    content = [
        TurnTextBlock(text="question"),
        TurnSkillBlock(path="demo/skill"),
    ]
    assert compile_human_text(content) == "question\n[skill:demo/skill]"
