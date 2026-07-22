#!/usr/bin/env python3
"""A1 gate: verify create_agent + AsyncPostgresSaver system message checkpoint behavior."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from langchain.agents import create_agent
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from app.agent.runtime.checkpointer import create_checkpointer


def _summarize(messages: list[BaseMessage]) -> dict[str, object]:
    system_msgs = [m for m in messages if isinstance(m, SystemMessage)]
    return {
        "total": len(messages),
        "system_count": len(system_msgs),
        "types": [type(m).__name__ for m in messages],
        "duplicate_system": len(system_msgs) > 1,
    }


async def main() -> int:
    static_system = "STATIC_SYSTEM_PROMPT_FOR_VERIFY"
    thread_id = "verify-chat-system-check-001"

    async with create_checkpointer() as checkpointer:
        llm = FakeListChatModel(responses=["assistant_reply_1", "assistant_reply_2"])
        agent = create_agent(
            llm,
            system_prompt=static_system,
            checkpointer=checkpointer,
            name="verify_system_checkpoint",
        )
        config = {"configurable": {"thread_id": thread_id}}
        await agent.ainvoke({"messages": [HumanMessage(content="user_turn_1")]}, config)
        state_after_1 = await agent.aget_state(config)
        await agent.ainvoke({"messages": [HumanMessage(content="user_turn_2")]}, config)
        state_after_2 = await agent.aget_state(config)
        try:
            await checkpointer.adelete_thread(thread_id)
        except Exception as exc:
            print(f"warning: adelete_thread failed: {exc}")

    msgs_1 = list(state_after_1.values.get("messages") or [])
    msgs_2 = list(state_after_2.values.get("messages") or [])
    summary_1 = _summarize(msgs_1)
    summary_2 = _summarize(msgs_2)

    print("=== create_agent + AsyncPostgresSaver system checkpoint verify ===")
    print(f"thread_id: {thread_id}")
    print("after invoke #1:", summary_1)
    print("after invoke #2:", summary_2)
    print("duplicate_system_messages:", summary_2["duplicate_system"])
    print("system_in_checkpoint:", summary_2["system_count"] > 0)

    if summary_2["duplicate_system"]:
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
