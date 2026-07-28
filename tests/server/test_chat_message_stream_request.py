from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.contracts.turn_content import (
    TurnMediaBlock,
    TurnMediaOrigin,
    TurnNodeBlock,
    TurnTextBlock,
    TurnUserInput,
    validate_turn_user_input,
)
from app.server.chat.schemas import MessageStreamRequest


def test_validate_turn_user_input_rejects_node_when_disallowed() -> None:
    with pytest.raises(ValueError, match="node blocks are not supported"):
        validate_turn_user_input(
            TurnUserInput(
                content=[TurnTextBlock(text="hi"), TurnNodeBlock(node_id="n-1")],
                materials=[],
            ),
            allow_node=False,
        )


def test_message_stream_request_has_no_attachment_ids_field() -> None:
    assert "attachment_ids" not in MessageStreamRequest.model_fields


def test_message_stream_request_rejects_attachment_ids_extra() -> None:
    with pytest.raises(ValidationError):
        MessageStreamRequest.model_validate(
            {
                "request_id": "550e8400-e29b-41d4-a716-446655440000",
                "conversation_id": 1,
                "content": [{"type": "text", "text": "hello"}],
                "materials": [],
                "model": "gpt-4",
                "attachment_ids": [1, 2],
            }
        )


def test_message_stream_request_accepts_materials() -> None:
    body = MessageStreamRequest.model_validate(
        {
            "request_id": "550e8400-e29b-41d4-a716-446655440000",
            "conversation_id": 1,
            "content": [{"type": "text", "text": "describe this"}],
            "materials": [
                {
                    "type": "image",
                    "origin": "upload",
                    "assetId": 42,
                }
            ],
            "model": "gpt-4",
        }
    )
    assert len(body.materials) == 1
    assert isinstance(body.materials[0], TurnMediaBlock)
    assert body.materials[0].asset_id == 42
    assert body.materials[0].origin == TurnMediaOrigin.UPLOAD
