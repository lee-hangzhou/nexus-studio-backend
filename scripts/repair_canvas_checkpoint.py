#!/usr/bin/env python3
"""修复画布 checkpoint 中未配对的 tool_calls (orphan assistant tool_calls)."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from langchain.agents import create_agent
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.agent.canvas.turn.checkpoint import repair_canvas_checkpoint_if_needed
from app.agent.runtime.checkpointer import create_checkpointer
from app.server.infra.config import settings


def _summarize_orphans(messages: list) -> int:
    pending: set[str] = set()
    orphans = 0
    for message in messages:
        if isinstance(message, AIMessage):
            pending = {
                str(call.get("id") or "")
                for call in (message.tool_calls or [])
                if call.get("id")
            }
        elif isinstance(message, ToolMessage):
            call_id = str(message.tool_call_id or "")
            if call_id in pending:
                pending.discard(call_id)
        elif isinstance(message, HumanMessage) and pending:
            orphans += len(pending)
            pending = set()
    return orphans + len(pending)


async def _run(project_id: int) -> int:
    thread_id = f"{settings.CANVAS_CHECKPOINT_THREAD_PREFIX}-{project_id}"
    async with create_checkpointer() as checkpointer:
        agent = create_agent(
            FakeListChatModel(responses=["ok"]),
            checkpointer=checkpointer,
            name="canvas_checkpoint_repair",
        )
        config = {"configurable": {"thread_id": thread_id}}
        before = list((await agent.aget_state(config)).values.get("messages") or [])
        orphan_count = _summarize_orphans(before)
        print(f"thread={thread_id} messages={len(before)} orphan_tool_calls={orphan_count}")
        if orphan_count == 0:
            print("nothing to repair")
            return 0
        repaired = await repair_canvas_checkpoint_if_needed(
            agent,
            config,
            project_id=project_id,
            turn_id="repair-script",
        )
        after = list((await agent.aget_state(config)).values.get("messages") or [])
        print(f"repaired={repaired} messages_after={len(after)} orphan_after={_summarize_orphans(after)}")
        return 0 if _summarize_orphans(after) == 0 else 1


def main() -> None:
    parser = argparse.ArgumentParser(description="Repair orphan tool_calls in canvas checkpoint")
    parser.add_argument("--project-id", type=int, required=True)
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_run(args.project_id)))


if __name__ == "__main__":
    main()
