from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.agent.chat.gate.assets import preflight_qr_locator
from app.agent.chat.gate.qr_verify import GateCaptureError


@pytest.mark.asyncio
async def test_preflight_qr_locator_passes_workspace(tmp_path: Path) -> None:
    with patch(
        "app.agent.chat.gate.assets.browser_runtime.locator_bounding_box",
        new=AsyncMock(return_value={"width": 120, "height": 120}),
    ) as bbox:
        await preflight_qr_locator(
            conversation_id=1,
            workspace=tmp_path,
            image_selector=".qr",
        )
    bbox.assert_awaited_once_with(1, ".qr", workspace=tmp_path)


@pytest.mark.asyncio
async def test_preflight_qr_locator_missing_bbox_raises(tmp_path: Path) -> None:
    with patch(
        "app.agent.chat.gate.assets.browser_runtime.locator_bounding_box",
        new=AsyncMock(return_value=None),
    ):
        with pytest.raises(GateCaptureError):
            await preflight_qr_locator(
                conversation_id=1,
                workspace=tmp_path,
                image_selector=".qr",
            )
