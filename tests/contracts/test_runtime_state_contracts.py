import pytest

from app.chat.turn.gate_emit import interrupt_value_is_user_gate
from app.services.generation_result import normalize_generation_result
from app.contracts.gateway import GatewayGenerateCallback


def test_gate_without_recovery_id_cannot_interrupt_turn() -> None:
    malformed_gate = {
        "gate_type": "confirm",
        "prompt": "continue?",
        "fields": [],
        "assets": {},
        "choices": None,
    }

    with pytest.raises(ValueError):
        interrupt_value_is_user_gate(malformed_gate)


def test_success_callback_without_results_is_downgraded_before_persistence() -> None:
    payload = GatewayGenerateCallback(taskId=9, status=5, reason=None, urls=[])

    result = normalize_generation_result(payload.status, payload.urls, payload.reason)

    assert result.status == 6
    assert result.error_message == "成功终态缺少生成结果"
