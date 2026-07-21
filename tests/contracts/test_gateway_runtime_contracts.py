import pytest

from app.chat.llm.openai_compat import OpenAICompatAdapter
from app.chat.llm.stream_assembler import OpenAIStreamAssembler
from app.chat.llm.thinking import ThinkingConfig
from app.core.embeddings import GatewayEmbeddingError, GatewayEmbeddingsClient
from app.core.gateway_errors import GatewayChatError


def test_malformed_chat_responses_fail_at_gateway_boundary() -> None:
    adapter = OpenAICompatAdapter()

    for payload in (
        "plain text",
        {"choices": []},
        {"choices": [{}]},
        {
            "choices": [
                {
                    "message": {
                        "content": None,
                        "tool_calls": [{"function": {"name": "read_file", "arguments": "{}"}}],
                    }
                }
            ]
        },
    ):
        with pytest.raises(GatewayChatError):
            adapter.parse_response(payload)


def test_malformed_sse_and_incomplete_tool_calls_stop_model_step() -> None:
    assembler = OpenAIStreamAssembler(thinking=ThinkingConfig())
    with pytest.raises(GatewayChatError):
        assembler.feed_sse_data("{not-json")

    assembler.feed_sse_data(
        '{"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"{}"}}]}}]}'
    )
    with pytest.raises(GatewayChatError):
        assembler.finish()


def test_usage_only_sse_frame_accepts_additive_fields_without_interrupting_stream() -> None:
    assembler = OpenAIStreamAssembler(thinking=ThinkingConfig())

    assembler.feed_sse_data('{"choices":[{"delta":{"content":"hello"}}]}')
    assert assembler.feed_sse_data(
        '{"choices":[],"usage":{"prompt_tokens":1,"completion_tokens":2,"total_tokens":3},"future_field":true}'
    ) == ([], [])
    assembler.feed_sse_data('{"choices":[{"delta":{},"finish_reason":"stop"}]}')
    assembler.feed_sse_data("[DONE]")

    assembled = assembler.finish()
    assert assembled.message.content == "hello"
    assert assembled.finish_reason == "stop"


@pytest.mark.parametrize(
    "data",
    (
        '{"choices":[]}',
        '{"choices":[],"usage":null}',
        '{"choices":[],"usage":[]}',
    ),
)
def test_empty_choices_sse_frame_requires_valid_usage(data: str) -> None:
    assembler = OpenAIStreamAssembler(thinking=ThinkingConfig())

    with pytest.raises(GatewayChatError):
        assembler.feed_sse_data(data)


def test_embedding_indexes_count_and_dimensions_are_enforced() -> None:
    duplicate_index = {
        "data": [
            {"object": "embedding", "index": 0, "embedding": [0.1, 0.2]},
            {"object": "embedding", "index": 0, "embedding": [0.3, 0.4]},
        ]
    }
    with pytest.raises(GatewayEmbeddingError):
        GatewayEmbeddingsClient._parse_vectors(
            duplicate_index,
            expected_count=2,
            expected_dimensions=2,
        )

    wrong_dimension = {
        "data": [{"object": "embedding", "index": 0, "embedding": [0.1]}]
    }
    with pytest.raises(GatewayEmbeddingError):
        GatewayEmbeddingsClient._parse_vectors(
            wrong_dimension,
            expected_count=1,
            expected_dimensions=2,
        )
