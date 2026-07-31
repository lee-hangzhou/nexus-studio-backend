from __future__ import annotations

from app.server.exceptions.codes import ErrorCode
from app.server.workshop.persistence.repository import (
    WorkshopProjectNotFoundError,
    WorkshopTaskConflictError,
    WorkshopTaskNotFoundError,
)
from app.server.workshop.services.http_errors import map_workshop_error
from app.server.workshop.services.task_orchestrator import TaskOrchestratorError
from app.server.workshop.services.workflow_schedule_service import WorkshopWorkflowScheduleError
from app.server.workshop.services.workshop_project_service import WorkshopProjectError


def test_map_workshop_not_found_and_conflict_codes() -> None:
    """工坊错误映射到可区分 ErrorCode"""
    assert map_workshop_error(WorkshopProjectNotFoundError("wp")).code == int(
        ErrorCode.RESOURCE_NOT_FOUND
    )
    assert map_workshop_error(WorkshopTaskNotFoundError("t")).code == int(ErrorCode.TASK_NOT_FOUND)
    assert map_workshop_error(WorkshopTaskConflictError("t")).code == int(ErrorCode.WORKSHOP_CONFLICT)
    assert map_workshop_error(WorkshopProjectError("unknown project: wp")).code == int(
        ErrorCode.RESOURCE_NOT_FOUND
    )
    assert map_workshop_error(TaskOrchestratorError("cannot begin execution from aligning")).code == int(
        ErrorCode.INVALID_PARAMS
    )
    assert map_workshop_error(TaskOrchestratorError("task conflict: t1")).code == int(
        ErrorCode.WORKSHOP_CONFLICT
    )
    assert map_workshop_error(WorkshopWorkflowScheduleError("unknown schedule: s")).code == int(
        ErrorCode.RESOURCE_NOT_FOUND
    )
    assert map_workshop_error(KeyError("research_advisor_x")).code == int(ErrorCode.INVALID_PARAMS)
