#!/usr/bin/env python3
"""Backfill durable assets for successful generation tasks.

The command is a dry run unless --apply is provided. Asset creation is
idempotent because AssetService deduplicates by user, storage key, source, and
source id.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.database import db
from app.domain.enums import GatewayTaskStatus
from app.models.generate_task import GenerateTask
from app.services.generate import generate_service


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="write assets and update result_asset_ids")
    parser.add_argument("--user-id", type=int, default=None, help="limit the backfill to one user")
    parser.add_argument("--limit", type=int, default=None, help="process at most this many tasks")
    return parser.parse_args()


async def run(args: argparse.Namespace) -> None:
    await db.connect()
    try:
        query = GenerateTask.filter(
            status=GatewayTaskStatus.SUCCEEDED,
        )
        if args.user_id is not None:
            query = query.filter(user_id=args.user_id)

        tasks = await query.order_by("id").all()
        tasks = [
            task
            for task in tasks
            if isinstance(task.result_keys, list)
            and task.result_keys
            and not (isinstance(task.result_asset_ids, list) and task.result_asset_ids)
        ]
        if args.limit is not None:
            tasks = tasks[: args.limit]

        print(f"mode={'apply' if args.apply else 'dry-run'} tasks={len(tasks)}")
        if not args.apply:
            for task in tasks[:20]:
                print(f"task_id={task.id} user_id={task.user_id} kind={task.kind} results={len(task.result_keys)}")
            return

        updated = 0
        for task in tasks:
            asset_ids = await generate_service._ensure_result_assets(task)
            if asset_ids:
                updated += 1
                print(f"task_id={task.id} asset_ids={asset_ids}")
        print(f"updated={updated} skipped={len(tasks) - updated}")
    finally:
        await db.disconnect()


if __name__ == "__main__":
    asyncio.run(run(parse_args()))
