"""Mount-aware turn runner — unique product entry over AgentMountSpec."""

from __future__ import annotations

from collections.abc import AsyncIterator

from langchain_core.messages import BaseMessage
from langgraph.types import Command

from app.agent.runtime.mounts.context import MountTurnContext
from app.agent.runtime.mounts.spec import AgentMountSpec
from app.agent.runtime.stream.encoder import encode_sse_frame
from app.agent.runtime.stream.frames import StreamFrameType, create_stream_frame
from app.agent.runtime.turn_engine.entry import stream_prepared_turn
from app.agent.runtime.turn_engine.prepared import PreparedTurn
from app.server.chat.domain.stream_enums import StreamErrorCode
from app.server.infra.logger import logger


async def stream_agent_turn(
    mount: AgentMountSpec,
    ctx: MountTurnContext,
    *,
    input_messages: list[BaseMessage] | None = None,
    resume_command: Command | None = None,
    is_resume: bool = False,
    turn_prepared: bool = False,
) -> AsyncIterator[str]:
    """Schedule Mount callables + TurnEngine. No mount-name branches here."""
    turn_id = ctx.turn_id
    try:
        if not turn_prepared and mount.prepare_turn is not None:
            ctx = await mount.prepare_turn(ctx)

        agent = await mount.build_agent(ctx)
        guards = mount.build_guards(ctx)
        subscribers = list(mount.build_subscribers(ctx))
        runnable_config = mount.build_runnable_config(ctx)
        thread_id = mount.resolve_thread_id(ctx)
        terminal_policy = mount.build_terminal_policy(ctx)
        heartbeat = mount.resolve_heartbeat_interval_sec(ctx)

        resolved_messages = input_messages
        if resolved_messages is None and mount.resolve_input_messages is not None:
            resolved_messages = mount.resolve_input_messages(ctx)

        recovery_hook = (
            mount.build_recovery_hook(ctx) if mount.build_recovery_hook is not None else None
        )
        preview = (
            mount.build_preview_tool_result(ctx)
            if mount.build_preview_tool_result is not None
            else None
        )
        start_repair = (
            mount.resolve_on_turn_start_repair(ctx)
            if mount.resolve_on_turn_start_repair is not None
            else None
        )
        cleanup_repair = (
            mount.resolve_on_turn_cleanup_repair(ctx)
            if mount.resolve_on_turn_cleanup_repair is not None
            else None
        )
        mode = mount.resolve_mode(ctx) if mount.resolve_mode is not None else getattr(ctx, "mode", None)
        client_turn_id = (
            mount.resolve_client_turn_id(ctx)
            if mount.resolve_client_turn_id is not None
            else getattr(ctx, "client_turn_id", None)
        )

        prepared = PreparedTurn(
            agent=agent,
            turn_id=turn_id,
            conversation_id=ctx.conversation_id,
            user_id=ctx.user_id,
            runnable_config=runnable_config,
            thread_id=thread_id,
            guards=guards,
            cancel_event=ctx.cancel_event,
            subscribers=subscribers,
            heartbeat_interval_sec=heartbeat,
            input_messages=resolved_messages,
            resume_command=resume_command,
            client_turn_id=client_turn_id,
            mode=mode if isinstance(mode, str) or mode is None else str(mode),
            is_resume=is_resume,
            recovery_hook=recovery_hook,
            on_turn_start_repair=start_repair,
            on_turn_cleanup_repair=cleanup_repair,
            terminal_policy=terminal_policy,
            preview_tool_result=preview,
        )
        async for chunk in stream_prepared_turn(prepared):
            yield chunk
    except Exception as exc:
        logger.exception(
            "agent.turn.error",
            mount=mount.name,
            turn_id=turn_id,
            error=str(exc),
        )
        yield encode_sse_frame(
            create_stream_frame(
                type=StreamFrameType.ERROR,
                code=StreamErrorCode.INTERNAL.value,
                message="turn failed",
                turn_id=turn_id,
            )
        )
