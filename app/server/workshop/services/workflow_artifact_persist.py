"""工作流文件产物持久化：工作区文件 → OSS → workshop_artifacts(oss)。"""

from __future__ import annotations

from dataclasses import dataclass
from mimetypes import guess_type
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from app.server.assets.services.service import (
    ASSET_SOURCE_ASSISTANT_OUTPUT,
    asset_service,
)
from app.server.infra.object_storage import object_storage, safe_filename
from app.server.workshop.domain.enums import WorkshopArtifactStorageType
from app.server.workshop.domain.types import ArtifactSubmission
from app.server.workshop.persistence.repository import WorkshopRepository, new_id


class WorkshopObjectStorage(Protocol):
    """对象存储最小端口：工作流收口上传用"""

    @property
    def _is_configured(self) -> bool:
        """是否已配置可写后端"""
        ...

    async def put_file_path(
        self,
        storage_key: str,
        path: Path,
        content_type: str | None = None,
    ) -> None:
        """上传本地文件"""
        ...


class WorkshopAssetWriter(Protocol):
    """资产登记最小端口"""

    async def create_asset(
        self,
        *,
        user_id: int,
        storage_key: str,
        mime_type: str,
        asset_type: str | None = None,
        filename: str = "",
        source_type: str,
        source_id: str | int | None = None,
        project_id: int | None = None,
        metadata: dict | None = None,
        status: str = "ready",
    ) -> object:
        """登记资产读模型"""
        ...


@dataclass(frozen=True, slots=True)
class PersistedWorkflowFile:
    """一次上传并入库的工作流文件产物"""

    artifact_id: str
    name: str
    storage_key: str
    size_bytes: int


async def persist_workflow_output_files(
    *,
    repository: WorkshopRepository,
    project_id: str,
    user_id: int,
    run_id: str,
    workspace_root: Path,
    storage: WorkshopObjectStorage | None = None,
    assets: WorkshopAssetWriter | None = None,
) -> tuple[PersistedWorkflowFile, ...]:
    """扫描 run 输出目录，上传 OSS 后登记为 oss 产物；单文件闭环成功后删除本地暂存。

    fail-closed：目录存在且含非空文件时，对象存储未配置或上传失败不得退回 filesystem；
    上传/入库未完成前不删本地文件。
    """
    store = storage if storage is not None else object_storage
    asset_writer = assets if assets is not None else asset_service
    root = workspace_root.resolve()
    out_dir = (root / "workflow_outputs" / run_id).resolve()
    if not str(out_dir).startswith(str(root)):
        raise RuntimeError("output path escapes workspace")
    if not out_dir.is_dir():
        return ()

    existing = await repository.list_artifacts_for_project(
        project_id=project_id,
        user_id=user_id,
        task_id=run_id,
    )
    existing_names = {item.name for item in existing}
    persisted: list[PersistedWorkflowFile] = []
    pending_files = sorted(path for path in out_dir.rglob("*") if path.is_file())

    for path in pending_files:
        resolved = path.resolve()
        if not str(resolved).startswith(str(out_dir)):
            raise RuntimeError("output path escapes run directory")
        name = safe_filename(resolved.name)
        if name in existing_names:
            continue
        size_bytes = resolved.stat().st_size
        if size_bytes <= 0:
            continue
        if not store._is_configured:  # noqa: SLF001 — 与 publish_file 同契约
            raise RuntimeError("object storage not configured")

        artifact_id = new_id("art")
        mime_type = guess_type(name)[0] or "application/octet-stream"
        storage_key = (
            f"workshop/{user_id}/{project_id}/{run_id}/{uuid4().hex}/{name}"
        )
        await store.put_file_path(storage_key, resolved, content_type=mime_type)
        await asset_writer.create_asset(
            user_id=user_id,
            storage_key=storage_key,
            mime_type=mime_type,
            filename=name,
            source_type=ASSET_SOURCE_ASSISTANT_OUTPUT,
            source_id=artifact_id,
            metadata={
                "workshop_project_id": project_id,
                "workflow_run_id": run_id,
                "size": size_bytes,
            },
        )
        await repository.create_artifact(
            artifact_id=artifact_id,
            project_id=project_id,
            user_id=user_id,
            task_id=run_id,
            artifact=ArtifactSubmission(
                name=name,
                storage_type=WorkshopArtifactStorageType.OSS,
                storage_key=storage_key,
                size_bytes=size_bytes,
            ),
        )
        existing_names.add(name)
        persisted.append(
            PersistedWorkflowFile(
                artifact_id=artifact_id,
                name=name,
                storage_key=storage_key,
                size_bytes=size_bytes,
            )
        )
        resolved.unlink()

    _remove_empty_run_output_dir(out_dir=out_dir, workspace_root=root)
    return tuple(persisted)


def _remove_empty_run_output_dir(*, out_dir: Path, workspace_root: Path) -> None:
    """无残留文件时自底向上删除空目录；尚有文件则不动"""
    if not out_dir.is_dir():
        return
    if any(path.is_file() for path in out_dir.rglob("*")):
        return
    for path in sorted(
        (item for item in out_dir.rglob("*") if item.is_dir()),
        key=lambda item: len(item.parts),
        reverse=True,
    ):
        path.rmdir()
    out_dir.rmdir()
    parent = out_dir.parent
    if (
        parent.name == "workflow_outputs"
        and parent.parent.resolve() == workspace_root.resolve()
    ):
        try:
            parent.rmdir()
        except OSError:
            return
