from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from langchain_core.messages import BaseMessage

from app.chat.llm.thinking import ThinkingConfig, ThinkTagStreamState, TokenPiece
from app.chat.vision.refs import VisionBuildContext


class ModelAdapter(ABC):
    @abstractmethod
    def build_params(
        self,
        messages: List[BaseMessage],
        gateway_model: str,
        tools: Optional[List[Dict[str, Any]]] = None,
        *,
        stream: bool = False,
        vision_ctx: VisionBuildContext | None = None,
    ) -> Dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def parse_response(self, raw: Any) -> Dict[str, Any]:
        """返回 {content, tool_calls, usage}"""
        raise NotImplementedError

    def parse_stream_delta(
        self,
        delta: Dict[str, Any],
        *,
        thinking: ThinkingConfig,
        tag_state: ThinkTagStreamState,
    ) -> List[TokenPiece]:
        raise NotImplementedError
