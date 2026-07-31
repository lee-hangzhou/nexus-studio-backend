from __future__ import annotations

from app.server.exceptions.base import AppError
from app.server.exceptions.codes import ErrorCode
from app.server.workshop.persistence.repository import (
    WorkshopArtifactConflictError,
    WorkshopExpertNotFoundError,
    WorkshopGroupChatOwnershipError,
    WorkshopOwnershipError,
    WorkshopProjectConflictError,
    WorkshopProjectNotFoundError,
    WorkshopProposalNotFoundError,
    WorkshopRepositoryError,
    WorkshopScheduleNotFoundError,
    WorkshopScheduleTriggerConflictError,
    WorkshopTaskConflictError,
    WorkshopTaskNotFoundError,
    WorkshopWorkflowConflictError,
    WorkshopWorkflowNotFoundError,
)
from app.server.workshop.services.task_orchestrator import TaskOrchestratorError
from app.server.workshop.services.workflow_schedule_service import WorkshopWorkflowScheduleError
from app.server.workshop.services.workshop_project_service import WorkshopProjectError


def map_workshop_error(exc: BaseException) -> AppError:
    """将工坊领域/仓储异常映射为一次 AppError"""
    if isinstance(exc, AppError):
        return exc
    if isinstance(exc, WorkshopTaskNotFoundError):
        return AppError(ErrorCode.TASK_NOT_FOUND, str(exc) or "workshop task not found")
    if isinstance(exc, WorkshopGroupChatOwnershipError):
        return AppError(ErrorCode.RESOURCE_NOT_FOUND, str(exc) or "workshop resource not found")
    if isinstance(
        exc,
        (
            WorkshopProjectNotFoundError,
            WorkshopProposalNotFoundError,
            WorkshopExpertNotFoundError,
            WorkshopWorkflowNotFoundError,
            WorkshopScheduleNotFoundError,
            WorkshopOwnershipError,
        ),
    ):
        return AppError(ErrorCode.RESOURCE_NOT_FOUND, str(exc) or "workshop resource not found")
    if isinstance(
        exc,
        (
            WorkshopTaskConflictError,
            WorkshopWorkflowConflictError,
            WorkshopArtifactConflictError,
            WorkshopScheduleTriggerConflictError,
            WorkshopProjectConflictError,
        ),
    ):
        return AppError(ErrorCode.WORKSHOP_CONFLICT, str(exc) or "workshop conflict")
    if isinstance(exc, WorkshopProjectError):
        return _map_project_message(str(exc))
    if isinstance(exc, TaskOrchestratorError):
        return _map_task_message(str(exc))
    if isinstance(exc, WorkshopWorkflowScheduleError):
        return _map_workflow_message(str(exc))
    if isinstance(exc, KeyError):
        return AppError(ErrorCode.INVALID_PARAMS, f"unknown preset: {exc.args[0]}")
    if isinstance(exc, WorkshopRepositoryError):
        return AppError(ErrorCode.INVALID_PARAMS, str(exc) or "workshop repository error")
    if isinstance(exc, ValueError):
        return AppError(ErrorCode.INVALID_PARAMS, str(exc) or "invalid workshop params")
    raise TypeError(f"unmapped workshop error: {type(exc)!r}")


def _map_project_message(message: str) -> AppError:
    """映射项目/名册业务错误文案"""
    if message.startswith("unknown project") or message.startswith("unknown expert proposal"):
        return AppError(ErrorCode.RESOURCE_NOT_FOUND, message)
    if message.startswith("expert not on roster") or message.startswith("expert not in room"):
        return AppError(ErrorCode.RESOURCE_NOT_FOUND, message)
    if "group chat not found" in message or "not owned by user" in message:
        return AppError(ErrorCode.RESOURCE_NOT_FOUND, message)
    if "already bound" in message:
        return AppError(ErrorCode.WORKSHOP_CONFLICT, message)
    return AppError(ErrorCode.INVALID_PARAMS, message)


def _map_task_message(message: str) -> AppError:
    """映射任务编排业务错误文案"""
    if message.startswith("unknown task"):
        return AppError(ErrorCode.TASK_NOT_FOUND, message)
    if message.startswith("unknown proposal"):
        return AppError(ErrorCode.RESOURCE_NOT_FOUND, message)
    if "conflict" in message:
        return AppError(ErrorCode.WORKSHOP_CONFLICT, message)
    return AppError(ErrorCode.INVALID_PARAMS, message)


def _map_workflow_message(message: str) -> AppError:
    """映射工作流/定时业务错误文案"""
    if message.startswith("unknown workflow") or message.startswith("unknown schedule"):
        return AppError(ErrorCode.RESOURCE_NOT_FOUND, message)
    if "conflict" in message:
        return AppError(ErrorCode.WORKSHOP_CONFLICT, message)
    return AppError(ErrorCode.INVALID_PARAMS, message)
