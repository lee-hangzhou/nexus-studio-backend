#!/usr/bin/env python3
"""Aggregate turn timeline from DB messages for debugging."""

from __future__ import annotations

import argparse
import asyncio
import json

from tortoise import Tortoise

from app.server.infra.database import db
from app.server.persistence import TORTOISE_ORM_MODEL_MODULES


async def _run(turn_id: str) -> None:
    await Tortoise.init(db_url=db.build_url(), modules={"models": list(TORTOISE_ORM_MODEL_MODULES)})
    from app.server.chat.persistence.messages import ChatMessages

    rows = await ChatMessages.filter(metadata__contains={"turn_id": turn_id}).order_by("created_at")
    timeline = []
    for row in rows:
        meta = row.metadata or {}
        timeline.append(
            {
                "id": row.id,
                "role": row.role,
                "phase": meta.get("phase"),
                "content_len": len(row.content or ""),
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
        )
    print(json.dumps({"turn_id": turn_id, "timeline": timeline}, ensure_ascii=False, indent=2))
    await Tortoise.close_connections()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--turn-id", required=True)
    args = parser.parse_args()
    asyncio.run(_run(args.turn_id))


if __name__ == "__main__":
    main()
