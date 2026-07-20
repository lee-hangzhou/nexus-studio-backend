#!/usr/bin/env python3
"""Bind orphan assistant_tool attachments using assistant message metadata artifacts."""

from __future__ import annotations

import asyncio

from tortoise import Tortoise

from app.core.database import db
from app.domain.chat_enums import AttachmentSource
from app.models.chat_attachments import ChatAttachments
from app.models.chat_messages import ChatMessages


async def _run() -> None:
    await Tortoise.init(db_url=db.build_url(), modules={"models": ["app.models"]})

    orphans = await ChatAttachments.filter(
        message_id__isnull=True,
        source=AttachmentSource.ASSISTANT_TOOL.value,
    )
    fixed = 0
    for row in orphans:
        candidates = await ChatMessages.filter(
            conversation_id=row.conversation_id,
            metadata__contains={"phase": "final"},
        ).order_by("-created_at")
        for message in candidates:
            artifacts = (message.metadata or {}).get("artifacts") or []
            for artifact in artifacts:
                if int(artifact.get("attachment_id", -1)) == row.id:
                    row.message_id = message.id
                    await row.save(update_fields=["message_id"])
                    fixed += 1
                    break
            if row.message_id:
                break

    print(f"backfill fixed={fixed} scanned_orphans={len(orphans)}")
    await Tortoise.close_connections()


if __name__ == "__main__":
    asyncio.run(_run())
