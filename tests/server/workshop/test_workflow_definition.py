"""WorkflowDefinition 校验 seam：环、产物依赖、preset、fail closed。"""

from __future__ import annotations

import pytest

from app.server.workshop.domain.enums import (
    WorkshopArtifactStorageType,
    WorkshopToolCapability,
)
from app.server.workshop.domain.workflow_definition import (
    NodeAssignee,
    NodeInput,
    NodeOutput,
    WorkflowDefinition,
    WorkflowDefinitionError,
    WorkflowEdge,
    WorkflowNode,
    validate_workflow_definition,
)


def _node(
    node_id: str,
    *,
    preset: str = "ecom_market_competitor_advisor",
    inputs: tuple[NodeInput, ...] = (),
    outputs: tuple[NodeOutput, ...] = (),
    caps: tuple[WorkshopToolCapability, ...] = (),
    instruction: str = "do work",
) -> WorkflowNode:
    return WorkflowNode(
        id=node_id,
        title=node_id,
        instruction=instruction,
        assignee=NodeAssignee(preset_key=preset),
        inputs=inputs,
        outputs=outputs
        or (
            NodeOutput(
                name=f"{node_id}_out",
                storage_type=WorkshopArtifactStorageType.DB,
            ),
        ),
        external_capabilities=caps,
    )


def test_linear_two_node_definition_validates() -> None:
    definition = WorkflowDefinition(
        nodes=(
            _node(
                "n1",
                outputs=(
                    NodeOutput(
                        name="brief",
                        storage_type=WorkshopArtifactStorageType.DB,
                    ),
                ),
                caps=(WorkshopToolCapability.WEB_SEARCH,),
            ),
            _node(
                "n2",
                preset="ecom_listing_planner_executor",
                inputs=(
                    NodeInput(kind="artifact", name="brief", from_node_id="n1"),
                ),
                outputs=(
                    NodeOutput(
                        name="copy",
                        storage_type=WorkshopArtifactStorageType.DB,
                    ),
                ),
            ),
        ),
        edges=(WorkflowEdge(from_id="n1", to_id="n2"),),
        known_preset_keys=frozenset(
            {
                "ecom_market_competitor_advisor",
                "ecom_listing_planner_executor",
            }
        ),
        preset_allowlists={
            "ecom_market_competitor_advisor": frozenset(
                {WorkshopToolCapability.WEB_SEARCH}
            ),
            "ecom_listing_planner_executor": frozenset(),
        },
    )

    validate_workflow_definition(definition)


def test_cycle_rejected() -> None:
    definition = WorkflowDefinition(
        nodes=(_node("a"), _node("b")),
        edges=(
            WorkflowEdge(from_id="a", to_id="b"),
            WorkflowEdge(from_id="b", to_id="a"),
        ),
        known_preset_keys=frozenset({"ecom_market_competitor_advisor"}),
        preset_allowlists={
            "ecom_market_competitor_advisor": frozenset(),
        },
    )

    with pytest.raises(WorkflowDefinitionError, match="cycle"):
        validate_workflow_definition(definition)


def test_unknown_preset_rejected() -> None:
    definition = WorkflowDefinition(
        nodes=(_node("a", preset="not_a_real_preset"),),
        edges=(),
        known_preset_keys=frozenset({"ecom_market_competitor_advisor"}),
        preset_allowlists={"ecom_market_competitor_advisor": frozenset()},
    )

    with pytest.raises(WorkflowDefinitionError, match="unknown preset"):
        validate_workflow_definition(definition)


def test_undeclared_artifact_input_requires_from_node_id() -> None:
    """未声明端口的产物输入必须带 from_node_id"""
    definition = WorkflowDefinition(
        nodes=(
            _node(
                "n1",
                outputs=(
                    NodeOutput(
                        name="brief",
                        storage_type=WorkshopArtifactStorageType.DB,
                    ),
                ),
            ),
            _node(
                "n2",
                inputs=(NodeInput(kind="artifact", name="missing"),),
            ),
        ),
        edges=(WorkflowEdge(from_id="n1", to_id="n2"),),
        known_preset_keys=frozenset({"ecom_market_competitor_advisor"}),
        preset_allowlists={"ecom_market_competitor_advisor": frozenset()},
    )

    with pytest.raises(WorkflowDefinitionError, match="unknown artifact input"):
        validate_workflow_definition(definition)


def test_undeclared_artifact_input_with_ancestor_from_node_ok() -> None:
    """中间产物名可不预声明，只要 from_node_id 是祖先"""
    definition = WorkflowDefinition(
        nodes=(
            _node("n1"),
            _node(
                "n2",
                inputs=(
                    NodeInput(kind="artifact", name="ephemeral", from_node_id="n1"),
                ),
            ),
        ),
        edges=(WorkflowEdge(from_id="n1", to_id="n2"),),
        known_preset_keys=frozenset({"ecom_market_competitor_advisor"}),
        preset_allowlists={"ecom_market_competitor_advisor": frozenset()},
    )

    validate_workflow_definition(definition)


def test_capability_outside_preset_allowlist_rejected() -> None:
    definition = WorkflowDefinition(
        nodes=(
            _node(
                "n1",
                caps=(WorkshopToolCapability.BROWSER_WRITE,),
            ),
        ),
        edges=(),
        known_preset_keys=frozenset({"ecom_market_competitor_advisor"}),
        preset_allowlists={
            "ecom_market_competitor_advisor": frozenset(
                {WorkshopToolCapability.WEB_SEARCH}
            ),
        },
    )

    with pytest.raises(WorkflowDefinitionError, match="external_capabilities"):
        validate_workflow_definition(definition)
