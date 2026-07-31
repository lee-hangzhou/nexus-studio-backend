from __future__ import annotations

import pytest

from app.server.workshop.domain.enums import (
    WorkshopArtifactStorageType,
    WorkshopToolCapability,
)
from app.server.workshop.domain.external_tool_gate import (
    executor_may_use_external,
    is_external_capability,
)
from app.server.workshop.domain.types import ArtifactSubmission
from app.server.workshop.domain.weak_accept import evaluate_weak_accept


def test_executor_browser_write_denied_until_task_auth() -> None:
    """浏览器写在任务授权前拒绝，授权后放行"""
    granted: frozenset[WorkshopToolCapability] = frozenset()
    assert (
        executor_may_use_external(granted, WorkshopToolCapability.BROWSER_WRITE)
        is False
    )
    granted = frozenset({WorkshopToolCapability.BROWSER_WRITE})
    assert (
        executor_may_use_external(granted, WorkshopToolCapability.BROWSER_WRITE)
        is True
    )


def test_project_file_write_does_not_need_external_auth() -> None:
    """项目内文件写不需要外部授权"""
    assert (
        executor_may_use_external(
            frozenset(), WorkshopToolCapability.WRITE_PROJECT_FILES
        )
        is True
    )


def test_taobao_store_write_and_generation_submit_are_external() -> None:
    """淘天店铺写与 Generation submit 为外部能力"""
    assert is_external_capability(WorkshopToolCapability.TAOBAO_STORE_WRITE)
    assert is_external_capability(WorkshopToolCapability.GENERATION_SUBMIT)
    assert not is_external_capability(WorkshopToolCapability.GENERATION_LIST_MODELS)
    assert (
        executor_may_use_external(
            frozenset(), WorkshopToolCapability.TAOBAO_STORE_WRITE
        )
        is False
    )
    granted = frozenset(
        {
            WorkshopToolCapability.TAOBAO_STORE_WRITE,
            WorkshopToolCapability.GENERATION_SUBMIT,
        }
    )
    assert (
        executor_may_use_external(granted, WorkshopToolCapability.TAOBAO_STORE_WRITE)
        is True
    )
    assert (
        executor_may_use_external(granted, WorkshopToolCapability.BROWSER_WRITE) is False
    )


def test_weak_accept_compares_used_external_capabilities() -> None:
    """弱验收按已记录外部能力对照授权集合，无必需产物时可无产物通过"""
    ok = evaluate_weak_accept(
        goals=("g",),
        covered_goals=("g",),
        required_artifacts=(),
        artifacts=(),
        granted_external=frozenset({WorkshopToolCapability.MCP}),
        used_capabilities=(WorkshopToolCapability.MCP,),
    )
    assert ok.passed is True

    bad = evaluate_weak_accept(
        goals=("g",),
        covered_goals=("g",),
        required_artifacts=(),
        artifacts=(),
        granted_external=frozenset(),
        used_capabilities=(WorkshopToolCapability.BROWSER_WRITE,),
    )
    assert bad.passed is False
    assert "external_auth_violation" in bad.reasons


def test_weak_accept_requires_explicit_required_artifacts() -> None:
    """必需产物缺失失败，无必需产物则可通过"""
    missing = evaluate_weak_accept(
        goals=("g",),
        covered_goals=("g",),
        required_artifacts=("report.md",),
        artifacts=(),
        granted_external=frozenset(),
        used_capabilities=(),
    )
    assert missing.passed is False
    assert "artifacts_missing" in missing.reasons

    present = evaluate_weak_accept(
        goals=("g",),
        covered_goals=("g",),
        required_artifacts=("report.md",),
        artifacts=(
            ArtifactSubmission(
                name="report.md",
                storage_type=WorkshopArtifactStorageType.DB,
                content="hello",
            ),
        ),
        granted_external=frozenset(),
        used_capabilities=(),
    )
    assert present.passed is True

    free = evaluate_weak_accept(
        goals=("g",),
        covered_goals=("g",),
        required_artifacts=(),
        artifacts=(),
        granted_external=frozenset(),
        used_capabilities=(),
    )
    assert free.passed is True


def test_db_artifact_submission_rejects_empty_content() -> None:
    """DB 产物构造时拒绝空内容"""
    with pytest.raises(ValueError, match="db artifact content required"):
        ArtifactSubmission(
            name="report.md",
            storage_type=WorkshopArtifactStorageType.DB,
            content="",
        )
    with pytest.raises(ValueError, match="db artifact content required"):
        ArtifactSubmission(
            name="report.md",
            storage_type=WorkshopArtifactStorageType.DB,
            content="   ",
        )


def test_oss_artifact_submission_requires_storage_key_and_size() -> None:
    """OSS 产物构造时要求 storage_key 与 size_bytes，禁止 inline content"""
    with pytest.raises(ValueError, match="external artifact storage_key required"):
        ArtifactSubmission(
            name="file.bin",
            storage_type=WorkshopArtifactStorageType.OSS,
            size_bytes=100,
        )
    with pytest.raises(ValueError, match="external artifact size_bytes required"):
        ArtifactSubmission(
            name="file.bin",
            storage_type=WorkshopArtifactStorageType.OSS,
            storage_key="oss://bucket/key",
        )
    with pytest.raises(ValueError, match="external artifact must not carry inline content"):
        ArtifactSubmission(
            name="file.bin",
            storage_type=WorkshopArtifactStorageType.OSS,
            storage_key="oss://bucket/key",
            size_bytes=100,
            content="data",
        )
