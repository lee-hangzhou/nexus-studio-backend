"""Canvas arrange / inspect_node_media / skill index 对齐的最小测试"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agent.chat.tools.result import ToolResult
from app.agent.runtime.skills.assembler import assemble_canvas_skills_block, build_turn_skill_library
from app.agent.runtime.skills.library import CanvasSkillLibrary
from app.agent.runtime.skills.models import SkillDefinition
from app.agent.runtime.skills.registry import CanvasSkillRegistry
from app.agent.runtime.skills.skill_tools import build_read_canvas_skill_tool
from app.contracts.canvas import CanvasPatchResponse
from app.contracts.turn_content import (
    TurnMediaOrigin,
    TurnMediaType,
    TurnReferenceAsset,
    TurnReferenceIndex,
    TurnReferenceSection,
)
from app.server.exceptions.base import AppError
from app.server.exceptions.codes import ErrorCode


def test_manual_confirm_tools_exclude_arrange_and_edge() -> None:
    from app.server.infra.config import settings

    tools = settings.canvas_manual_confirm_tools
    assert "apply_canvas_patch" in tools
    assert "submit_node_generation" in tools
    assert "apply_canvas_edge_operation" not in tools
    assert "apply_canvas_arrange" not in tools


def test_write_tool_descriptions_require_read_canvas_skill() -> None:
    from app.agent.canvas.tools.canvas_arrange import build_apply_canvas_arrange_tool
    from app.agent.canvas.tools.canvas_write import (
        build_apply_canvas_edge_operation_tool,
        build_apply_canvas_patch_tool,
    )
    from app.agent.canvas.tools.generate_models import build_list_generate_models_tool
    from app.agent.canvas.tools.generation import build_submit_node_generation_tool

    holder: dict[str, str | None] = {"turn_id": "t1"}
    patch = build_apply_canvas_patch_tool(
        project_id=1, episode_id=1, user_id=1, turn_id_holder=holder
    )
    edge = build_apply_canvas_edge_operation_tool(
        project_id=1, episode_id=1, user_id=1, turn_id_holder=holder
    )
    arrange = build_apply_canvas_arrange_tool(
        project_id=1, episode_id=1, user_id=1, turn_id_holder=holder
    )
    submit = build_submit_node_generation_tool(project_id=1, episode_id=1, user_id=1)
    list_models = build_list_generate_models_tool()

    assert "read_canvas_skill" in (patch.description or "")
    assert "canvas_operations" in (patch.description or "")
    assert "read_canvas_skill" in (edge.description or "")
    assert "canvas_operations" in (edge.description or "")
    assert "read_canvas_skill" in (arrange.description or "")
    assert "canvas_operations" in (arrange.description or "")
    assert "read_canvas_skill" in (submit.description or "")
    assert "canvas_generation" in (submit.description or "")
    assert "param_options" in (list_models.description or "")


@pytest.mark.asyncio
async def test_apply_canvas_arrange_success_batch(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.agent.canvas.tools.canvas_arrange import build_apply_canvas_arrange_tool

    port = AsyncMock()
    response = CanvasPatchResponse(nodes=[], edges=[], deleted_node_ids=[], deleted_edge_ids=[])
    port.apply_patch = AsyncMock(return_value=response)
    monkeypatch.setattr("app.agent.canvas.tools.canvas_arrange.get_canvas_port", lambda: port)

    tool = build_apply_canvas_arrange_tool(
        project_id=1,
        episode_id=2,
        user_id=3,
        turn_id_holder={"turn_id": "turn-1"},
    )
    raw = await tool.ainvoke(
        {
            "moves": [
                {
                    "id": "11111111-1111-1111-1111-111111111111",
                    "revision": 2,
                    "position": {"x": 10, "y": 20},
                }
            ],
        }
    )
    result = ToolResult.parse_tool_message(raw)
    assert result.success is True
    payload = json.loads(result.output)
    assert "moved" not in payload
    assert "conflicts" not in payload
    assert set(payload.keys()) >= {"nodes", "edges", "deleted_node_ids", "deleted_edge_ids"}
    port.apply_patch.assert_awaited_once()
    kwargs = port.apply_patch.await_args.kwargs
    assert len(kwargs["ops"]) == 1
    assert kwargs["ops"][0].op == "update_node"


@pytest.mark.asyncio
async def test_apply_canvas_arrange_rejects_invalid_uuid() -> None:
    from app.agent.canvas.tools.canvas_arrange import build_apply_canvas_arrange_tool

    tool = build_apply_canvas_arrange_tool(
        project_id=1, episode_id=1, user_id=1, turn_id_holder={"turn_id": None}
    )
    with pytest.raises(Exception):
        await tool.ainvoke(
            {
                "moves": [
                    {"id": "not-a-uuid", "revision": 1, "position": {"x": 0, "y": 0}},
                ]
            }
        )


@pytest.mark.asyncio
async def test_apply_canvas_arrange_over_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.agent.canvas.tools.canvas_arrange import build_apply_canvas_arrange_tool
    from app.server.infra.config import settings

    monkeypatch.setattr(settings, "CANVAS_ARRANGE_MAX_OPS", 1)
    port = AsyncMock()
    port.apply_patch = AsyncMock()
    monkeypatch.setattr("app.agent.canvas.tools.canvas_arrange.get_canvas_port", lambda: port)

    tool = build_apply_canvas_arrange_tool(
        project_id=1, episode_id=1, user_id=1, turn_id_holder={"turn_id": None}
    )
    raw = await tool.ainvoke(
        {
            "moves": [
                {
                    "id": "11111111-1111-1111-1111-111111111111",
                    "revision": 1,
                    "position": {"x": 0, "y": 0},
                },
                {
                    "id": "22222222-2222-2222-2222-222222222222",
                    "revision": 1,
                    "position": {"x": 1, "y": 1},
                },
            ]
        }
    )
    result = ToolResult.parse_tool_message(raw)
    assert result.success is False
    assert result.error_type == "invalid_arguments"
    port.apply_patch.assert_not_awaited()


@pytest.mark.asyncio
async def test_apply_canvas_arrange_revision_conflict_is_whole_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.agent.canvas.tools.canvas_arrange import build_apply_canvas_arrange_tool

    details = {"conflicts": [{"kind": "node", "id": "11111111-1111-1111-1111-111111111111"}]}
    port = AsyncMock()
    port.apply_patch = AsyncMock(
        side_effect=AppError(ErrorCode.CANVAS_REVISION_CONFLICT, details=details)
    )
    monkeypatch.setattr("app.agent.canvas.tools.canvas_arrange.get_canvas_port", lambda: port)

    tool = build_apply_canvas_arrange_tool(
        project_id=1, episode_id=1, user_id=1, turn_id_holder={"turn_id": "t"}
    )
    raw = await tool.ainvoke(
        {
            "moves": [
                {
                    "id": "11111111-1111-1111-1111-111111111111",
                    "revision": 1,
                    "position": {"x": 0, "y": 0},
                }
            ]
        }
    )
    result = ToolResult.parse_tool_message(raw)
    assert result.success is False
    assert result.error_type == "revision_conflict"
    assert result.error_detail is not None
    assert json.loads(result.error_detail) == details
    assert "moved" not in result.output


@pytest.mark.asyncio
async def test_inspect_node_media_no_outputs_and_missing_node(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.agent.canvas.tools.inspect_node_media import build_inspect_node_media_tool

    port = AsyncMock()
    empty_node = MagicMock()
    empty_node.id = "11111111-1111-1111-1111-111111111111"
    empty_node.data = {"title": "x"}
    graph = MagicMock()
    graph.nodes = [empty_node]
    port.get_graph = AsyncMock(return_value=graph)
    monkeypatch.setattr("app.agent.canvas.tools.inspect_node_media.get_canvas_port", lambda: port)
    monkeypatch.setattr(
        "app.agent.canvas.tools.inspect_node_media.get_assets_port",
        lambda: AsyncMock(),
    )

    tool = build_inspect_node_media_tool(project_id=1, episode_id=9, user_id=1)
    raw = await tool.ainvoke(
        {
            "node_ids": ["11111111-1111-1111-1111-111111111111"],
            "task": "describe",
        }
    )
    result = ToolResult.parse_tool_message(raw)
    assert result.success is False
    assert result.error_type == "file_not_found"
    port.get_graph.assert_awaited()
    assert port.get_graph.await_args.kwargs["project_id"] == 1

    graph.nodes = []
    raw2 = await tool.ainvoke(
        {
            "node_ids": ["22222222-2222-2222-2222-222222222222"],
            "task": "describe",
        }
    )
    result2 = ToolResult.parse_tool_message(raw2)
    assert result2.success is False
    assert result2.error_type == "invalid_node_id"


def test_inspect_node_media_not_mounted_when_tools_disabled() -> None:
    from app.agent.canvas.tools.build import build_canvas_inspect_only_tools, build_canvas_tools

    library = CanvasSkillLibrary(
        [
            SkillDefinition(
                name="canvas_operations",
                description="ops",
                priority=1,
                always_load=False,
                body="body",
            )
        ]
    )
    full = build_canvas_tools(
        project_id=1,
        episode_id=1,
        user_id=1,
        turn_id_holder={"turn_id": None},
        tool_asset_ids=frozenset(),
        asset_media_types={},
        skill_library=library,
    )
    names = {t.name for t in full}
    assert "inspect_node_media" in names
    assert "read_canvas_skill" in names
    assert "apply_canvas_arrange" in names

    inspect_only = build_canvas_inspect_only_tools(
        user_id=1,
        tool_asset_ids=frozenset({1}),
        asset_media_types={1: TurnMediaType.IMAGE},
    )
    inspect_names = {t.name for t in inspect_only}
    assert "inspect_turn_media" in inspect_names
    assert "inspect_node_media" not in inspect_names
    assert "read_canvas_skill" not in inspect_names


def test_canvas_skill_registry_loads_and_fail_fast(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    CanvasSkillRegistry.reset()
    skills = CanvasSkillRegistry.load()
    names = {s.name for s in skills}
    assert names == {
        "canvas_generation",
        "canvas_operations",
        "canvas_response_style",
        "canvas_turn_references",
        "canvas_workflow",
    }
    assert any(s.always_load for s in skills if s.name == "canvas_response_style")
    assert any(s.always_load for s in skills if s.name == "canvas_workflow")
    assert all(not s.always_load for s in skills if s.name in {"canvas_operations", "canvas_generation"})

    CanvasSkillRegistry.reset()
    bad_root = tmp_path / "skills"
    bad_dir = bad_root / "broken_skill"
    bad_dir.mkdir(parents=True)
    (bad_dir / "SKILL.md").write_text("# no frontmatter\n", encoding="utf-8")
    monkeypatch.setattr(CanvasSkillRegistry, "skills_dir", classmethod(lambda cls: bad_root))
    with pytest.raises(RuntimeError, match="frontmatter"):
        CanvasSkillRegistry.load()
    CanvasSkillRegistry.reset()

    missing_meta = tmp_path / "skills2" / "no_priority"
    missing_meta.mkdir(parents=True)
    (missing_meta / "SKILL.md").write_text(
        "---\nname: no_priority\ndescription: x\nalways_load: false\n---\n\nbody\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        CanvasSkillRegistry,
        "skills_dir",
        classmethod(lambda cls: missing_meta.parent),
    )
    with pytest.raises(RuntimeError, match="priority"):
        CanvasSkillRegistry.load()
    CanvasSkillRegistry.reset()


@pytest.mark.asyncio
async def test_read_canvas_skill_known_and_unknown() -> None:
    library = CanvasSkillLibrary(
        [
            SkillDefinition(
                name="canvas_operations",
                description="ops",
                priority=1,
                always_load=False,
                body="# operations body",
            )
        ]
    )
    tool = build_read_canvas_skill_tool(library)
    ok = ToolResult.parse_tool_message(await tool.ainvoke({"name": "canvas_operations"}))
    assert ok.success is True
    assert "operations body" in ok.output

    missing = ToolResult.parse_tool_message(await tool.ainvoke({"name": "nope"}))
    assert missing.success is False
    assert missing.error_type == "skill_not_found"


def test_turn_references_skill_inlined_when_refs_present(monkeypatch: pytest.MonkeyPatch) -> None:
    CanvasSkillRegistry.reset()
    monkeypatch.setattr(
        CanvasSkillRegistry,
        "load",
        classmethod(
            lambda cls: [
                SkillDefinition(
                    name="canvas_response_style",
                    description="style",
                    priority=-100,
                    always_load=True,
                    body="style-body",
                ),
                SkillDefinition(
                    name="canvas_turn_references",
                    description="refs",
                    priority=15,
                    always_load=False,
                    body="turn-ref-body",
                ),
                SkillDefinition(
                    name="canvas_workflow",
                    description="flow",
                    priority=10,
                    always_load=True,
                    body="workflow-body",
                ),
            ]
        ),
    )
    empty = TurnReferenceIndex()
    library_empty = build_turn_skill_library(reference_index=empty)
    block_empty = assemble_canvas_skills_block(
        skill_library=library_empty,
        user_index_items=[],
        selected_skills=[],
    )
    assert "turn-ref-body" not in block_empty
    assert "style-body" in block_empty
    assert "workflow-body" in block_empty
    assert "name=canvas_turn_references" in block_empty
    assert block_empty.index("## Skill Index usage") < block_empty.index("style-body")
    assert block_empty.index("style-body") < block_empty.index("workflow-body")

    with_refs = TurnReferenceIndex(
        inline=TurnReferenceSection(
            assets=[
                TurnReferenceAsset(
                    asset_id=1,
                    media_type=TurnMediaType.IMAGE,
                    origin=TurnMediaOrigin.UPLOAD,
                )
            ]
        )
    )
    library_refs = build_turn_skill_library(reference_index=with_refs)
    block_refs = assemble_canvas_skills_block(
        skill_library=library_refs,
        user_index_items=[],
        selected_skills=[],
    )
    assert "turn-ref-body" in block_refs
    assert block_refs.index("## Skill Index usage") < block_refs.index("style-body")
    assert block_refs.index("style-body") < block_refs.index("workflow-body")
    assert block_refs.index("workflow-body") < block_refs.index("turn-ref-body")
    # shared registry cache must not be mutated
    cached = CanvasSkillRegistry.load()
    turn_skill = next(s for s in cached if s.name == "canvas_turn_references")
    assert turn_skill.always_load is False


def test_canvas_ui_preview_covers_new_tools() -> None:
    from app.agent.chat.tools.ui_preview import sanitize_tool_step_preview
    from app.agent.chat.tools.result import ToolResult

    arrange = ToolResult.ok(
        json.dumps({"nodes": [], "edges": [], "deleted_node_ids": [], "deleted_edge_ids": []})
    ).to_tool_message()
    assert sanitize_tool_step_preview("apply_canvas_arrange", arrange, ok=True) == "已整理画布布局"

    edge = ToolResult.ok(
        json.dumps({"nodes": [], "edges": [], "deleted_node_ids": [], "deleted_edge_ids": []})
    ).to_tool_message()
    assert sanitize_tool_step_preview("apply_canvas_edge_operation", edge, ok=True) == "已更新连线"

    models = ToolResult.ok(json.dumps({"items": [{"model_id": "m1"}]})).to_tool_message()
    assert sanitize_tool_step_preview("list_generate_models", models, ok=True) == "已查询 1 个可用模型"
    # 契约是 items[]；错误字段 models[] 不得当作计数成功路径
    stale = ToolResult.ok(json.dumps({"models": [{"model_id": "m1"}]})).to_tool_message()
    assert sanitize_tool_step_preview("list_generate_models", stale, ok=True) == "已查询可用模型"

    inspect = ToolResult.ok(
        json.dumps({"task": "describe", "analysis": "x", "node_ids": [], "asset_ids": []})
    ).to_tool_message()
    assert sanitize_tool_step_preview("inspect_node_media", inspect, ok=True) == "已完成视觉理解"

    skill = ToolResult.ok("# body").to_tool_message()
    assert sanitize_tool_step_preview("read_canvas_skill", skill, ok=True) == "已读取画布技能"
