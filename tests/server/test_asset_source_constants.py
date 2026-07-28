from app.server.assets.services.service import (
    ASSET_SOURCE_AGENT_UPLOAD,
    ASSET_SOURCE_ASSISTANT_OUTPUT,
    ASSET_SOURCE_CHAT_UPLOAD,
    ASSET_SOURCE_GENERATE_MATERIAL,
    ASSET_SOURCE_GENERATE_RESULT,
    ASSET_SOURCE_MANUAL_UPLOAD,
    LIBRARY_SOURCE_TYPES,
    NON_LIBRARY_SOURCE_TYPES,
)


def test_library_and_non_library_source_types_are_disjoint() -> None:
    assert LIBRARY_SOURCE_TYPES == frozenset(
        {ASSET_SOURCE_MANUAL_UPLOAD, ASSET_SOURCE_GENERATE_RESULT}
    )
    assert NON_LIBRARY_SOURCE_TYPES == frozenset(
        {
            ASSET_SOURCE_CHAT_UPLOAD,
            ASSET_SOURCE_AGENT_UPLOAD,
            ASSET_SOURCE_ASSISTANT_OUTPUT,
            ASSET_SOURCE_GENERATE_MATERIAL,
        }
    )
    assert LIBRARY_SOURCE_TYPES.isdisjoint(NON_LIBRARY_SOURCE_TYPES)


def test_library_filter_covers_library_sources_only() -> None:
    """library 语义覆盖 LIBRARY_SOURCE_TYPES；all 不再隐式等于 library"""
    assert "library" not in LIBRARY_SOURCE_TYPES
    assert ASSET_SOURCE_CHAT_UPLOAD not in LIBRARY_SOURCE_TYPES
    assert ASSET_SOURCE_MANUAL_UPLOAD in LIBRARY_SOURCE_TYPES
