from app.chat.llm.anthropic import AnthropicAdapter
from app.chat.llm.openai_compat import OpenAICompatAdapter
from app.chat.llm.registry import ModelSpec, get_model_spec, list_models_for_api, load_model_registry

__all__ = [
    "AnthropicAdapter",
    "OpenAICompatAdapter",
    "ModelSpec",
    "get_adapter",
    "get_model_spec",
    "list_models_for_api",
    "load_model_registry",
]


def get_adapter(family: str) -> AnthropicAdapter | OpenAICompatAdapter:
    if family == "anthropic":
        return AnthropicAdapter()
    return OpenAICompatAdapter()
