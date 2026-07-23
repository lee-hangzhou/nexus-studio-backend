"""GatewayChatModel.bind_tools must match LangChain/trustcall RunnableBinding contract."""

from __future__ import annotations

from app.agent.chat.llm.gateway_chat_model import GatewayChatModel
from app.agent.chat.llm.registry import ModelSpec
from app.agent.chat.llm.thinking import thinking_config_from_registry


def _llm() -> GatewayChatModel:
    return GatewayChatModel(
        model_key="test-model",
        spec=ModelSpec(
            family="openai",
            gateway_model="test-model",
            display_name="test",
            context_budget=128000,
            thinking=thinking_config_from_registry(None),
            supports_vision=False,
        ),
    )


_TOOL = {
    "type": "function",
    "function": {
        "name": "PatchDoc",
        "description": "patch",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}


def test_bind_tools_returns_runnable_with_bound() -> None:
    """trustcall accesses ``self.bound.bound.bind_tools`` when deletes are enabled."""
    llm = _llm()
    bound = llm.bind_tools([_TOOL], tool_choice="any")
    assert hasattr(bound, "bound")
    assert bound.bound is llm
    assert bound.kwargs["tools"][0]["function"]["name"] == "PatchDoc"
    assert bound.kwargs["tool_choice"] == "any"

    rebound = bound.bound.bind_tools(
        [_TOOL],
        tool_choice="any",
        parallel_tool_calls=True,
        strict=True,
    )
    assert hasattr(rebound, "bound")
    assert rebound.kwargs["tools"][0]["function"]["name"] == "PatchDoc"
    assert rebound.kwargs["parallel_tool_calls"] is True
    assert rebound.kwargs["strict"] is True
