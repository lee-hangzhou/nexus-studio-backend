from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from tortoise import Tortoise

from app.server.assets.persistence.assets import Assets
from app.server.assets.services.service import asset_service
from app.server.canvas.persistence.edges import CanvasEdges
from app.server.canvas.persistence.episode_meta import CanvasEpisodeMeta
from app.server.canvas.persistence.nodes import CanvasNodes
from app.server.canvas.persistence.operations import CanvasOperations
from app.server.canvas.services.canvas_service import canvas_service
from app.server.projects.domain.models import CanvasScope
from app.server.projects.persistence.episodes import ProjectEpisodes
from app.server.projects.persistence.projects import Projects
from app.server.projects.services.cover import cover_service

MODELS = [
    "app.server.assets.persistence.assets",
    "app.server.canvas.persistence.edges",
    "app.server.canvas.persistence.episode_meta",
    "app.server.canvas.persistence.nodes",
    "app.server.canvas.persistence.operations",
    "app.server.projects.persistence.episodes",
    "app.server.projects.persistence.projects",
]


async def _init_db() -> None:
    await Tortoise.init(db_url="sqlite://:memory:", modules={"models": MODELS})
    await Tortoise.generate_schemas()


async def _close_db() -> None:
    await Tortoise.close_connections()


async def _project(user_id: int = 1) -> Projects:
    return await Projects.create(
        owner_user_id=str(user_id),
        name="项目",
        status=1,
        cover_asset_id=None,
        tone_constraint={},
        style_constraint={},
        config={},
    )


async def _episode(project_id: int, user_id: int = 1) -> ProjectEpisodes:
    return await ProjectEpisodes.create(
        project_id=project_id,
        creator_id=user_id,
        episode_no=1,
        name="第 1 集",
        cover_asset_id=None,
    )


async def _asset(
    *,
    user_id: int,
    project_id: int | None,
    asset_type: str = "image",
    deleted_at: datetime | None = None,
) -> Assets:
    return await Assets.create(
        user_id=user_id,
        project_id=project_id,
        storage_key=f"assets/{user_id}/{uuid4().hex}",
        filename="cover.png",
        mime_type="image/png" if asset_type == "image" else "video/mp4",
        asset_type=asset_type,
        source_type="generate_result",
        source_id="100",
        metadata={},
        status="ready",
        deleted_at=deleted_at,
    )


@pytest.mark.asyncio
async def test_canvas_snapshot_presigns_only_scope_owned_project_assets(monkeypatch: pytest.MonkeyPatch) -> None:
    await _init_db()
    try:
        project = await _project(user_id=1)
        episode = await _episode(int(project.id), user_id=1)
        await CanvasEpisodeMeta.create(episode_id=int(episode.id))
        allowed = await _asset(user_id=1, project_id=int(project.id))
        foreign_user = await _asset(user_id=2, project_id=int(project.id))
        other_project = await _asset(user_id=1, project_id=999)
        node_id = uuid4()
        await CanvasNodes.create(
            id=node_id,
            episode_id=int(episode.id),
            kind="image",
            revision=1,
            position_x=0,
            position_y=0,
            data={
                "status": "success",
                "output_asset_ids": [int(foreign_user.id), int(allowed.id), int(other_project.id)],
            },
        )
        monkeypatch.setattr(asset_service, "preview_url", lambda key: f"signed:{key}")

        snapshot = await canvas_service.get_snapshot(
            CanvasScope(project_id=int(project.id), episode_id=int(episode.id), user_id=1)
        )

        assert snapshot.nodes[0].output_asset_urls == [f"signed:{allowed.storage_key}"]
    finally:
        await _close_db()


@pytest.mark.asyncio
async def test_generation_cover_fill_sets_empty_project_and_episode_once() -> None:
    await _init_db()
    try:
        project = await _project(user_id=1)
        episode = await _episode(int(project.id), user_id=1)
        foreign = await _asset(user_id=2, project_id=None)
        first = await _asset(user_id=1, project_id=None)
        second = await _asset(user_id=1, project_id=None)
        scope = CanvasScope(project_id=int(project.id), episode_id=int(episode.id), user_id=1)

        filled = await cover_service.fill_missing_cover_from_generation(
            scope,
            node_id="node-a",
            generation_task_id=100,
            asset_ids=[int(foreign.id), int(first.id)],
        )
        assert filled == int(first.id)
        assert (await Projects.get(id=project.id)).cover_asset_id == int(first.id)
        assert (await ProjectEpisodes.get(id=episode.id)).cover_asset_id == int(first.id)
        refreshed_asset = await Assets.get(id=first.id)
        assert refreshed_asset.project_id == int(project.id)
        assert refreshed_asset.metadata["episode_id"] == int(episode.id)

        refilled = await cover_service.fill_missing_cover_from_generation(
            scope,
            node_id="node-b",
            generation_task_id=101,
            asset_ids=[int(second.id)],
        )
        assert refilled is None
        assert (await Projects.get(id=project.id)).cover_asset_id == int(first.id)
        assert (await ProjectEpisodes.get(id=episode.id)).cover_asset_id == int(first.id)
    finally:
        await _close_db()


@pytest.mark.asyncio
async def test_deleted_asset_cover_cleanup_is_retryable_after_asset_already_deleted() -> None:
    await _init_db()
    try:
        project = await _project(user_id=1)
        episode = await _episode(int(project.id), user_id=1)
        deleted_asset = await _asset(
            user_id=1,
            project_id=int(project.id),
            deleted_at=datetime.now(timezone.utc),
        )
        await Projects.filter(id=project.id).update(cover_asset_id=int(deleted_asset.id))
        await ProjectEpisodes.filter(id=episode.id).update(cover_asset_id=int(deleted_asset.id))

        deleted_count = await asset_service.soft_delete_assets(user_id=1, asset_ids=[int(deleted_asset.id)])
        await cover_service.clear_deleted_asset_refs(user_id=1, asset_ids=[int(deleted_asset.id)])

        assert deleted_count == 0
        assert (await Projects.get(id=project.id)).cover_asset_id is None
        assert (await ProjectEpisodes.get(id=episode.id)).cover_asset_id is None
    finally:
        await _close_db()

