from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.server.workshop.domain.ecommerce.authorized_operations import (
    AuthorizedOperation,
    AuthorizedOperationError,
    append_authorized_operation,
    authorize_operation,
    consume_operation,
    list_authorized_operations,
)
from app.server.workshop.domain.enums import WorkshopToolCapability


def test_authorize_and_consume_operation() -> None:
    """匹配 hash 的 live op 可授权；消费后不可再用"""
    now = datetime.now(timezone.utc)
    op = AuthorizedOperation(
        id="op_1",
        task_id="task_1",
        capability=WorkshopToolCapability.TAOBAO_STORE_WRITE,
        operation_kind="publish",
        payload_hash="hash_abc",
        granted_by=1,
        granted_at=now,
    )
    brief = append_authorized_operation({}, op)
    ops = list_authorized_operations(brief)
    assert len(ops) == 1
    matched = authorize_operation(
        granted_external=frozenset({WorkshopToolCapability.TAOBAO_STORE_WRITE}),
        authorized_ops=ops,
        capability=WorkshopToolCapability.TAOBAO_STORE_WRITE,
        payload_hash="hash_abc",
        task_id="task_1",
    )
    assert matched.id == "op_1"
    brief = consume_operation(brief, operation_id="op_1", consumed_at=now)
    ops_after = list_authorized_operations(brief)
    with pytest.raises(AuthorizedOperationError):
        authorize_operation(
            granted_external=frozenset({WorkshopToolCapability.TAOBAO_STORE_WRITE}),
            authorized_ops=ops_after,
            capability=WorkshopToolCapability.TAOBAO_STORE_WRITE,
            payload_hash="hash_abc",
            task_id="task_1",
        )


def test_hash_mismatch_fails_closed() -> None:
    """payload_hash 不匹配 fail closed"""
    op = AuthorizedOperation(
        id="op_2",
        task_id="task_1",
        capability=WorkshopToolCapability.GENERATION_SUBMIT,
        operation_kind="generation_submit",
        payload_hash="hash_old",
        granted_by=1,
        granted_at=datetime.now(timezone.utc),
    )
    with pytest.raises(AuthorizedOperationError):
        authorize_operation(
            granted_external=frozenset({WorkshopToolCapability.GENERATION_SUBMIT}),
            authorized_ops=(op,),
            capability=WorkshopToolCapability.GENERATION_SUBMIT,
            payload_hash="hash_new",
            task_id="task_1",
        )
