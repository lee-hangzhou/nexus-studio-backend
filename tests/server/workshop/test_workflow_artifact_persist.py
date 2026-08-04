"""工作流文件产物收口 → OSS 的 seam 测试。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.server.workshop.domain.enums import WorkshopArtifactStorageType
from app.server.workshop.services.workflow_artifact_persist import (
    persist_workflow_output_files,
)


@pytest.mark.asyncio
async def test_persist_uploads_files_to_oss_and_registers_artifacts(
    tmp_path: Path,
) -> None:
    """非空输出文件必须上传对象存储并以 oss 产物入库"""
    run_id = "run_abc"
    out = tmp_path / "workflow_outputs" / run_id
    out.mkdir(parents=True)
    target = out / "小剧本.txt"
    target.write_text("hello script", encoding="utf-8")

    repository = MagicMock()
    repository.list_artifacts_for_project = AsyncMock(return_value=[])
    repository.create_artifact = AsyncMock(return_value="art_1")

    storage = MagicMock()
    storage._is_configured = True
    storage.put_file_path = AsyncMock()

    assets = MagicMock()
    assets.create_asset = AsyncMock(return_value=object())

    persisted = await persist_workflow_output_files(
        repository=repository,
        project_id="wp_1",
        user_id=7,
        run_id=run_id,
        workspace_root=tmp_path,
        storage=storage,
        assets=assets,
    )

    assert len(persisted) == 1
    assert persisted[0].name == "小剧本.txt"
    assert persisted[0].size_bytes == len("hello script".encode("utf-8"))
    assert persisted[0].storage_key.startswith("workshop/7/wp_1/run_abc/")
    storage.put_file_path.assert_awaited_once()
    put_key = storage.put_file_path.await_args.args[0]
    put_path = storage.put_file_path.await_args.args[1]
    assert put_key == persisted[0].storage_key
    assert put_path == target.resolve()

    assets.create_asset.assert_awaited_once()
    asset_kwargs = assets.create_asset.await_args.kwargs
    assert asset_kwargs["storage_key"] == persisted[0].storage_key
    assert asset_kwargs["filename"] == "小剧本.txt"

    repository.create_artifact.assert_awaited_once()
    art_kwargs = repository.create_artifact.await_args.kwargs
    submission = art_kwargs["artifact"]
    assert submission.storage_type is WorkshopArtifactStorageType.OSS
    assert submission.storage_key == persisted[0].storage_key
    assert submission.size_bytes == persisted[0].size_bytes
    assert art_kwargs["task_id"] == run_id
    assert not target.exists()
    assert not out.exists()


@pytest.mark.asyncio
async def test_persist_keeps_local_file_when_upload_fails(tmp_path: Path) -> None:
    """上传失败保留本地暂存，便于重试"""
    run_id = "run_keep_local"
    out = tmp_path / "workflow_outputs" / run_id
    out.mkdir(parents=True)
    target = out / "a.txt"
    target.write_text("x", encoding="utf-8")

    repository = MagicMock()
    repository.list_artifacts_for_project = AsyncMock(return_value=[])
    repository.create_artifact = AsyncMock()

    storage = MagicMock()
    storage._is_configured = True
    storage.put_file_path = AsyncMock(side_effect=RuntimeError("tos down"))

    with pytest.raises(RuntimeError, match="tos down"):
        await persist_workflow_output_files(
            repository=repository,
            project_id="wp_1",
            user_id=1,
            run_id=run_id,
            workspace_root=tmp_path,
            storage=storage,
            assets=MagicMock(create_asset=AsyncMock()),
        )

    assert target.exists()
    repository.create_artifact.assert_not_awaited()


@pytest.mark.asyncio
async def test_persist_fails_closed_when_storage_not_configured(tmp_path: Path) -> None:
    """有文件但 OSS 未配置时不得登记 filesystem，必须失败"""
    run_id = "run_no_oss"
    out = tmp_path / "workflow_outputs" / run_id
    out.mkdir(parents=True)
    target = out / "a.txt"
    target.write_text("x", encoding="utf-8")

    repository = MagicMock()
    repository.list_artifacts_for_project = AsyncMock(return_value=[])
    repository.create_artifact = AsyncMock()

    storage = MagicMock()
    storage._is_configured = False
    storage.put_file_path = AsyncMock()

    with pytest.raises(RuntimeError, match="object storage not configured"):
        await persist_workflow_output_files(
            repository=repository,
            project_id="wp_1",
            user_id=1,
            run_id=run_id,
            workspace_root=tmp_path,
            storage=storage,
            assets=MagicMock(create_asset=AsyncMock()),
        )

    storage.put_file_path.assert_not_awaited()
    repository.create_artifact.assert_not_awaited()
    assert target.exists()


@pytest.mark.asyncio
async def test_persist_returns_empty_when_output_dir_missing(tmp_path: Path) -> None:
    """无输出目录视为本节点无文件产物"""
    repository = MagicMock()
    repository.list_artifacts_for_project = AsyncMock(return_value=[])
    storage = MagicMock()
    storage._is_configured = True

    persisted = await persist_workflow_output_files(
        repository=repository,
        project_id="wp_1",
        user_id=1,
        run_id="missing",
        workspace_root=tmp_path,
        storage=storage,
        assets=MagicMock(),
    )
    assert persisted == ()
