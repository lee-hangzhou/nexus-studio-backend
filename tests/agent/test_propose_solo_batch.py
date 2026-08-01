"""propose_upgrade_and_invite 必须与其它工具隔离同批"""

from __future__ import annotations

from app.agent.chat.agent.gate_solo import batch_violation
from app.agent.chat.tools.judgment_gate import PROPOSE_UPGRADE_AND_INVITE


def test_propose_solo_rejects_mixed_batch() -> None:
    assert batch_violation(
        [
            {"id": "1", "name": PROPOSE_UPGRADE_AND_INVITE},
            {"id": "2", "name": "web_search"},
        ]
    )


def test_propose_solo_allows_alone() -> None:
    assert not batch_violation([{"id": "1", "name": PROPOSE_UPGRADE_AND_INVITE}])


def test_non_solo_tools_not_flagged() -> None:
    assert not batch_violation(
        [
            {"id": "1", "name": "web_search"},
            {"id": "2", "name": "read_file"},
        ]
    )
