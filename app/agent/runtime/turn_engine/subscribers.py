from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from typing import Protocol, runtime_checkable

from app.agent.runtime.stream.frames import StreamFrame
from app.agent.runtime.turn_engine.events import TurnEvent, TurnEventKind
from app.server.infra.logger import logger

TurnEmit = Callable[[StreamFrame], Awaitable[None]]


class TurnSubscriber(Protocol):
    barrier_events: frozenset[TurnEventKind]
    broadcast_events: frozenset[TurnEventKind]

    async def handle(self, event: TurnEvent, *, emit: TurnEmit) -> None:
        ...


@runtime_checkable
class StartableTurnSubscriber(Protocol):
    async def start(self, *, emit: TurnEmit, turn_id: str) -> None:
        ...


@runtime_checkable
class ClosableTurnSubscriber(Protocol):
    async def close(self) -> None:
        ...


class TurnEventBus:
    def __init__(self, subscribers: Sequence[TurnSubscriber] = ()) -> None:
        self._subscribers = list(subscribers)
        self._broadcast_tasks: set[asyncio.Task[None]] = set()
        self._subscriber_chains: dict[int, asyncio.Task[None]] = {}

    async def start(self, *, emit: TurnEmit, turn_id: str) -> None:
        for subscriber in self._subscribers:
            if isinstance(subscriber, StartableTurnSubscriber):
                await subscriber.start(emit=emit, turn_id=turn_id)

    async def barrier(self, event: TurnEvent, *, emit: TurnEmit) -> None:
        for subscriber in self._subscribers:
            if event.kind in subscriber.barrier_events:
                previous = self._subscriber_chains.get(id(subscriber))
                if previous is not None:
                    try:
                        await previous
                    except Exception as exc:
                        logger.exception(
                            "turn.subscriber.previous_failed",
                            subscriber=subscriber.__class__.__name__,
                            event=event.kind.value,
                            turn_id=event.turn_id,
                            error=str(exc),
                        )
                await subscriber.handle(event, emit=emit)

    def broadcast(self, event: TurnEvent, *, emit: TurnEmit) -> None:
        for subscriber in self._subscribers:
            if event.kind not in subscriber.broadcast_events:
                continue
            subscriber_key = id(subscriber)
            previous = self._subscriber_chains.get(subscriber_key)
            task = asyncio.create_task(
                self._run_broadcast_chained(previous, subscriber, event, emit=emit)
            )
            self._subscriber_chains[subscriber_key] = task
            self._broadcast_tasks.add(task)
            task.add_done_callback(self._broadcast_tasks.discard)
            task.add_done_callback(
                lambda done, key=subscriber_key: self._clear_subscriber_chain(key, done)
            )

    async def drain(self) -> None:
        if not self._broadcast_tasks:
            return
        await asyncio.gather(*list(self._broadcast_tasks), return_exceptions=True)

    async def close(self) -> None:
        await self.drain()
        for subscriber in self._subscribers:
            if isinstance(subscriber, ClosableTurnSubscriber):
                try:
                    await subscriber.close()
                except Exception as exc:
                    logger.exception(
                        "turn.subscriber.close_failed",
                        subscriber=subscriber.__class__.__name__,
                        error=str(exc),
                    )

    async def _run_broadcast(self, subscriber: TurnSubscriber, event: TurnEvent, *, emit: TurnEmit) -> None:
        try:
            await subscriber.handle(event, emit=emit)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception(
                "turn.subscriber.broadcast_failed",
                subscriber=subscriber.__class__.__name__,
                event=event.kind.value,
                turn_id=event.turn_id,
                error=str(exc),
            )

    async def _run_broadcast_chained(
        self,
        previous: asyncio.Task[None] | None,
        subscriber: TurnSubscriber,
        event: TurnEvent,
        *,
        emit: TurnEmit,
    ) -> None:
        if previous is not None:
            try:
                await previous
            except Exception as exc:
                logger.exception(
                    "turn.subscriber.previous_broadcast_failed",
                    subscriber=subscriber.__class__.__name__,
                    event=event.kind.value,
                    turn_id=event.turn_id,
                    error=str(exc),
                )
        await self._run_broadcast(subscriber, event, emit=emit)

    def _clear_subscriber_chain(self, subscriber_key: int, task: asyncio.Task[None]) -> None:
        if self._subscriber_chains.get(subscriber_key) is task:
            self._subscriber_chains.pop(subscriber_key, None)
