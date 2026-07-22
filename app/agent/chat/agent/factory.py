from typing import List, Optional

from langchain.agents import create_agent
from langchain.agents.middleware import SummarizationMiddleware
from langchain_core.tools import StructuredTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph
from langgraph.store.base import BaseStore

from app.agent.chat.agent.gate_solo_middleware import GateSoloBatchMiddleware
from app.agent.chat.agent.prompts import DEFAULT_CHAT_SYSTEM
from app.agent.chat.llm.gateway_chat_model import GatewayChatModel
from app.server.infra.config import settings


def build_chat_agent(
    llm: GatewayChatModel,
    tools: List[StructuredTool],
    checkpointer: BaseCheckpointSaver,
    system_prompt: Optional[str] = None,
    *,
    store: BaseStore | None = None,
    summarization_llm: GatewayChatModel | None = None,
) -> CompiledStateGraph:
    middleware: list = [
        GateSoloBatchMiddleware(),
        SummarizationMiddleware(
            model=summarization_llm or llm,
            trigger=("tokens", settings.chat_max_tokens_before_summary),
            keep=("tokens", settings.CHAT_SUMMARY_KEEP_TOKENS),
            trim_tokens_to_summarize=settings.CHAT_SUMMARY_TRIM_TO_SUMMARIZE,
        )
    ]
    return create_agent(
        llm,
        tools=tools or None,
        system_prompt=system_prompt or DEFAULT_CHAT_SYSTEM,
        middleware=middleware,
        debug=False,
        name="nexus_chat_agent",
        checkpointer=checkpointer,
        store=store,
    )
