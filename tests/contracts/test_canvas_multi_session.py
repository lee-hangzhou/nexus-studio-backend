from __future__ import annotations

import pytest
from tortoise import Tortoise

from app.agent.canvas.turn.lock import canvas_turn_lock
from app.server.canvas.domain.enums import CanvasSessionStatus
from app.server.canvas.persistence.messages import CanvasMessages
from app.server.canvas.persistence.sessions import CanvasSessions
from app.server.canvas.services.canvas_service import canvas_service
from app.server.canvas.services.session_service import canvas_session_service
from app.server.chat.domain.enums import ChatMessageRole
from app.server.exceptions.base import AppError
from app.server.exceptions.codes import ErrorCode
from app.server.projects.domain.models import CanvasScope
from app.server.projects.persistence.episodes import ProjectEpisodes
from app.server.projects.persistence.projects import Projects

_MODELS = [
    "app.server.projects.persistence.projects",
    "app.server.projects.persistence.episodes",
    "app.server.canvas.persistence.sessions",
    "app.server.canvas.persistence.messages",
]


async def _init_db() -> None:
    await Tortoise.init(
        db_url="sqlite://:memory:",
        modules={"models": _MODELS},
    )
    await Tortoise.generate_schemas()


def _scope(episode_id: int, user_id: int = 1, project_id: int = 1) -> CanvasScope:
    return CanvasScope(project_id=project_id, episode_id=episode_id, user_id=user_id)


async def _episode() -> tuple[int, int]:
    project = await Projects.create(
        owner_user_id="1",
        name="项目",
        status=1,
        cover_asset_id=None,
        tone_constraint={},
        style_constraint={},
        config={},
    )
    episode = await ProjectEpisodes.create(
        project_id=int(project.id),
        creator_id=1,
        episode_no=1,
        name="第 1 集",
        cover_asset_id=None,
    )
    return int(project.id), int(episode.id)


@pytest.mark.asyncio
async def test_ensure_default_session_is_idempotent() -> None:
    await _init_db()
    try:
        _, episode_id = await _episode()
        scope = _scope(episode_id)
        a = await canvas_session_service.ensure_default_session(scope)
        b = await canvas_session_service.ensure_default_session(scope)
        assert a.id == b.id
        assert a.is_default is True
        rows = await CanvasSessions.filter(
            episode_id=episode_id,
            user_id=1,
            status=CanvasSessionStatus.ACTIVE,
        )
        assert len(rows) == 1
    finally:
        await Tortoise.close_connections()


@pytest.mark.asyncio
async def test_messages_isolated_by_session() -> None:
    await _init_db()
    try:
        _, episode_id = await _episode()
        scope = _scope(episode_id)
        s1 = await canvas_session_service.ensure_default_session(scope)
        s2 = await canvas_session_service.create_session(scope, title="二号会话")
        await CanvasMessages.create(
            episode_id=episode_id,
            session_id=s1.id,
            user_id=1,
            role=int(ChatMessageRole.USER),
            content="a",
            metadata={},
        )
        await CanvasMessages.create(
            episode_id=episode_id,
            session_id=s2.id,
            user_id=1,
            role=int(ChatMessageRole.USER),
            content="b",
            metadata={},
        )
        m1 = await canvas_service.list_messages(scope, session_id=s1.id, before_id=None, limit=50)
        m2 = await canvas_service.list_messages(scope, session_id=s2.id, before_id=None, limit=50)
        assert [x.content for x in m1] == ["a"]
        assert [x.content for x in m2] == ["b"]
    finally:
        await Tortoise.close_connections()


@pytest.mark.asyncio
async def test_session_turn_locks_are_independent(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.server.canvas.domain.constants import CANVAS_SESSION_TURN_LOCK_KEY_TEMPLATE

    key_a = CANVAS_SESSION_TURN_LOCK_KEY_TEMPLATE.format(session_id=11)
    key_b = CANVAS_SESSION_TURN_LOCK_KEY_TEMPLATE.format(session_id=22)
    assert key_a != key_b
    assert "session_turn_lock" in key_a

    class _FakeRedis:
        def __init__(self) -> None:
            self._store: dict[str, str] = {}

        async def set(self, key: str, value: str, ex: int | None = None, nx: bool = False) -> bool | None:
            del ex
            if nx and key in self._store:
                return False
            self._store[key] = str(value)
            return True

        async def get(self, key: str) -> str | None:
            return self._store.get(key)

        async def delete(self, *keys: str) -> int:
            n = 0
            for key in keys:
                if key in self._store:
                    del self._store[key]
                    n += 1
            return n

        @property
        def client(self) -> "_FakeRedis":
            return self

        async def eval(self, script: str, numkeys: int, *args: str) -> int:
            del script, numkeys
            key, owner = args[0], args[1]
            if self._store.get(key) != owner:
                return 0
            del self._store[key]
            return 1

    fake = _FakeRedis()
    monkeypatch.setattr("app.agent.canvas.turn.lock.redis_client", fake)
    monkeypatch.setattr(
        "app.agent.canvas.turn.lock.settings",
        type("S", (), {"CANVAS_TURN_LOCK_TTL_SEC": 30})(),
    )

    await canvas_turn_lock.acquire(11, "t1")
    await canvas_turn_lock.acquire(22, "t2")
    with pytest.raises(AppError) as exc:
        await canvas_turn_lock.acquire(11, "t3")
    assert exc.value.code == int(ErrorCode.CANVAS_SESSION_BUSY)
    await canvas_turn_lock.release(11, "t1")
    await canvas_turn_lock.release(22, "t2")


@pytest.mark.asyncio
async def test_ensure_prefers_is_default_not_latest_active() -> None:
    await _init_db()
    try:
        _, episode_id = await _episode()
        scope = _scope(episode_id)
        default = await canvas_session_service.ensure_default_session(scope)
        await canvas_session_service.create_session(scope, title="旁路会话")
        again = await canvas_session_service.ensure_default_session(scope)
        assert again.id == default.id
        assert again.is_default is True
    finally:
        await Tortoise.close_connections()


@pytest.mark.asyncio
async def test_closed_session_readable_and_create_placeholder_title() -> None:
    await _init_db()
    try:
        _, episode_id = await _episode()
        scope = _scope(episode_id)
        default = await canvas_session_service.ensure_default_session(scope)
        created = await canvas_session_service.create_session(scope)
        assert created.title.startswith("新会话 ·")
        await CanvasMessages.create(
            episode_id=episode_id,
            session_id=created.id,
            user_id=1,
            role=int(ChatMessageRole.USER),
            content="closed-history",
            metadata={},
        )
        row = await CanvasSessions.get(id=created.id)
        await canvas_session_service.soft_close_session(row)
        owned = await canvas_session_service.require_owned_session(
            scope, created.id, allow_closed=True
        )
        assert owned.status == int(CanvasSessionStatus.CLOSED)
        msgs = await canvas_service.list_messages(
            scope, session_id=created.id, before_id=None, limit=50
        )
        assert [m.content for m in msgs] == ["closed-history"]
        assert default.is_default is True
    finally:
        await Tortoise.close_connections()


@pytest.mark.asyncio
async def test_touch_session_bumps_updated_at() -> None:
    await _init_db()
    try:
        _, episode_id = await _episode()
        scope = _scope(episode_id)
        session = await canvas_session_service.ensure_default_session(scope)
        row = await CanvasSessions.get(id=session.id)
        before = row.updated_at
        await canvas_session_service.touch_session(
            episode_id=episode_id, session_id=session.id, user_id=1
        )
        await row.refresh_from_db()
        assert row.updated_at is not None
        if before is not None:
            assert row.updated_at >= before
    finally:
        await Tortoise.close_connections()


def test_canvas_session_title_frame_contract() -> None:
    from app.contracts.stream import CanvasSessionTitleFrame
    from app.server.chat.domain.stream_enums import StreamFrameType

    frame = CanvasSessionTitleFrame(
        type=StreamFrameType.CANVAS_SESSION_TITLE,
        session_id=9,
        title="镜头节奏",
        updated_at="2026-07-27T00:00:00+00:00",
    )
    dumped = frame.model_dump(mode="json")
    assert dumped["type"] == "canvas_session_title"
    assert dumped["session_id"] == 9
    assert "conversation_id" not in dumped
