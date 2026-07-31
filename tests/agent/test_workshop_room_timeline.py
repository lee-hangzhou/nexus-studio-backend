from __future__ import annotations

from app.agent.workshop.room_timeline import RoomTimelineMessage, build_room_timeline_block


def test_room_timeline_block_includes_user_host_and_expert() -> None:
    """群时间线块同时含用户、助手与专家发言要点"""
    block = build_room_timeline_block(
        [
            RoomTimelineMessage(role="user", content="先问助手预算"),
            RoomTimelineMessage(role="assistant", content="建议先定客单价", speaker_name="项目助手"),
            RoomTimelineMessage(role="assistant", content="竞品价带在 89-129", speaker_name="市场与竞品研究"),
        ]
    )
    assert "本群已发生对话" in block
    assert "用户：先问助手预算" in block
    assert "项目助手：建议先定客单价" in block
    assert "市场与竞品研究：竞品价带在 89-129" in block
    assert "工具" not in block


def test_room_timeline_block_empty_when_no_messages() -> None:
    """无消息时不注入空壳标题"""
    assert build_room_timeline_block([]) == ""
