"""Canvas empty-answer recovery: fail via engine (SseTurnSubscriber owns ERROR)."""

from __future__ import annotations

from langchain_core.runnables import RunnableConfig
from langgraph.graph.state import CompiledStateGraph

from app.agent.runtime.agent.events import ModelStepFinishedEvent, TurnCompletedEvent
from app.agent.runtime.turn.enums import TurnTerminatedBy
from app.agent.runtime.turn_engine.run_state import RunState


class CanvasEmptyAnswerHook:
    """Reject completed turns with no answer text — no SSE emission."""

    async def after_model_step(
        self,
        event: ModelStepFinishedEvent,
        *,
        state: RunState,
    ) -> TurnTerminatedBy | None:
        del event, state
        return None

    async def before_complete(
        self,
        event: TurnCompletedEvent,
        *,
        state: RunState,
    ) -> TurnTerminatedBy | None:
        del event
        if "".join(state.answer_parts).strip():
            return None
        return TurnTerminatedBy.EMPTY_RESPONSE

    async def on_stream_break(
        self,
        terminated_by: TurnTerminatedBy,
        *,
        state: RunState,
        agent: CompiledStateGraph,
        runnable_config: RunnableConfig,
    ) -> TurnTerminatedBy | None:
        del terminated_by, state, agent, runnable_config
        return None
