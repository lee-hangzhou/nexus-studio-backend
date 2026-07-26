from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.agent.canvas.turn import memory_background as mb
from app.agent.runtime.memory.instructions import FIXED_PROJECT_EXTRACT_INSTRUCTIONS
from app.agent.runtime.memory.secrets import scan_text_for_secrets


def test_extract_skips_when_model_and_turn_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mb.settings, "MEMORY_STORE_ENABLED", True)
    monkeypatch.setattr(mb.settings, "MEMORY_EXTRACT_MODEL", "")
    with patch.object(mb, "get_memory_store", return_value=MagicMock()):
        with patch.object(mb.background_supervisor, "start") as start:
            mb.schedule_canvas_memory_extract(
                user_text="记住女主是医生",
                answer_text="好的，已记下。",
                user_id=1,
                project_id=2,
                turn_id="t1",
                turn_model_key="",
            )
            start.assert_not_called()


def test_extract_falls_back_to_turn_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mb.settings, "MEMORY_STORE_ENABLED", True)
    monkeypatch.setattr(mb.settings, "MEMORY_EXTRACT_MODEL", "")
    store = MagicMock()
    with patch.object(mb, "get_memory_store", return_value=store):
        with patch.object(mb.model_catalog, "has", return_value=True):
            with patch.object(mb, "scan_text_for_secrets", return_value=[]):
                with patch.object(mb.background_supervisor, "start") as start:

                    def _consume(coro, **kwargs):
                        coro.close()

                    start.side_effect = _consume
                    mb.schedule_canvas_memory_extract(
                        user_text="女主改成医生",
                        answer_text="已更新设定。",
                        user_id=9,
                        project_id=88,
                        turn_id="turn-1",
                        turn_model_key="deepseek-v4-pro",
                    )
                    start.assert_called_once()


def test_extract_skips_on_detect_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mb.settings, "MEMORY_STORE_ENABLED", True)
    monkeypatch.setattr(mb.settings, "MEMORY_EXTRACT_MODEL", "gpt-5.5")
    with patch.object(mb, "get_memory_store", return_value=MagicMock()):
        with patch.object(mb.model_catalog, "has", return_value=True):
            with patch.object(
                mb, "scan_text_for_secrets", return_value=[MagicMock(secret_type="AWS Key")]
            ):
                with patch.object(mb.background_supervisor, "start") as start:
                    mb.schedule_canvas_memory_extract(
                        user_text="token=AKIAIOSFODNN7EXAMPLE",
                        answer_text="ok",
                        user_id=1,
                        project_id=2,
                        turn_id="t1",
                    )
                    start.assert_not_called()


def test_extract_not_in_catalog_does_not_raise_or_schedule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mb.settings, "MEMORY_STORE_ENABLED", True)
    monkeypatch.setattr(mb.settings, "MEMORY_EXTRACT_MODEL", "not-in-catalog")
    with patch.object(mb, "get_memory_store", return_value=MagicMock()):
        with patch.object(mb.model_catalog, "has", return_value=False):
            with patch.object(mb.background_supervisor, "start") as start:
                mb.schedule_canvas_memory_extract(
                    user_text="女主改成医生",
                    answer_text="已更新设定。",
                    user_id=1,
                    project_id=2,
                    turn_id="t1",
                )
                start.assert_not_called()


def test_extract_schedules_with_serial_key_and_pair(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mb.settings, "MEMORY_STORE_ENABLED", True)
    monkeypatch.setattr(mb.settings, "MEMORY_EXTRACT_MODEL", "gpt-5.5")
    monkeypatch.setattr(mb.settings, "MEMORY_EXTRACTION_TIMEOUT_SEC", 60)
    store = MagicMock()

    with patch.object(mb, "get_memory_store", return_value=store):
        with patch.object(mb.model_catalog, "has", return_value=True):
            with patch.object(mb, "scan_text_for_secrets", return_value=[]):
                with patch.object(mb.background_supervisor, "start") as start:

                    def _consume(coro, **kwargs):
                        coro.close()

                    start.side_effect = _consume
                    mb.schedule_canvas_memory_extract(
                        user_text="女主改成医生",
                        answer_text="已更新设定。",
                        user_id=9,
                        project_id=88,
                        turn_id="turn-1",
                    )
                    start.assert_called_once()
                    kwargs = start.call_args.kwargs
                    assert kwargs["serial_key"] == "canvas.project:9:88"
                    assert "canvas-memory-extract-88" in kwargs["name"]


def test_require_extract_model_rejects_missing_catalog() -> None:
    with patch.object(mb.model_catalog, "has", return_value=False):
        with pytest.raises(mb.MemoryExtractModelError, match="catalog"):
            mb.require_extract_model_key("unknown")


def test_benign_profile_text_not_flagged_as_secret() -> None:
    hits = scan_text_for_secrets("我叫lee, 是nexus studio的开发者，也就是这个系统的开发者")
    assert hits == []


def test_fixed_instructions_forbid_profile_inference() -> None:
    text = FIXED_PROJECT_EXTRACT_INSTRUCTIONS.lower()
    assert "durable" in text
    assert "profile" in text or "画像" in FIXED_PROJECT_EXTRACT_INSTRUCTIONS
