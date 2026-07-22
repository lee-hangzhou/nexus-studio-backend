import pytest

from app.agent.chat.turn.gate_emit import interrupt_value_is_user_gate
from app.contracts.gateway import GatewayGenerateCallback, GatewayTaskStatusData
from app.server.generation.domain.terminal import normalize_gateway_terminal


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

    result = normalize_gateway_terminal(payload.status, payload.urls, payload.reason)

    assert result.status == 6
    assert result.error_message == "成功终态缺少生成结果"


def test_gateway_callback_and_task_status_accept_error_code() -> None:
    callback = GatewayGenerateCallback.model_validate(
        {"taskId": 11, "status": 6, "reason": "blocked", "errorCode": 20002}
    )
    assert callback.error_code == 20002
    assert callback.reason == "blocked"

    status = GatewayTaskStatusData.model_validate(
        {"taskId": 12, "status": 6, "reason": "gone", "errorCode": 10105}
    )
    assert status.error_code == 10105
