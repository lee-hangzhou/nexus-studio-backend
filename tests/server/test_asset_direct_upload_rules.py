from app.server.assets.domain.upload_rules import (
    MATERIAL_MAX_BYTES,
    assert_storage_key_owned,
    expected_key_prefix,
    validate_direct_upload_media,
)
from app.server.exceptions.base import AppError
from app.server.generation.domain.constants import MATERIAL_MAX_BYTES as GEN_MATERIAL_MAX_BYTES


def test_material_max_bytes_shared_by_generation() -> None:
    assert GEN_MATERIAL_MAX_BYTES is MATERIAL_MAX_BYTES


def test_validate_direct_upload_rejects_oversize() -> None:
    try:
        validate_direct_upload_media(mime_type="image/png", size=MATERIAL_MAX_BYTES + 1)
        raise AssertionError("expected AppError")
    except AppError as exc:
        assert "过大" in exc.message


def test_assert_storage_key_owned_rejects_cross_user() -> None:
    try:
        assert_storage_key_owned(
            storage_key="assets/8/abc/file.png",
            source_type="manual_upload",
            user_id=7,
            project_id=None,
        )
        raise AssertionError("expected AppError")
    except AppError as exc:
        assert "不匹配" in exc.message


def test_agent_prefix_requires_project() -> None:
    try:
        expected_key_prefix(source_type="agent_upload", user_id=1, project_id=None)
        raise AssertionError("expected AppError")
    except AppError as exc:
        assert "project_id" in exc.message
