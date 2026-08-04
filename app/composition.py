from app.agent.chat.service import ChatService
from app.agent.runtime.ports import configure_ports
from app.server.assets.persistence.repository import AssetRepository
from app.server.assets.services.service import asset_service
from app.server.chat.persistence.attachment_repository import ChatAttachmentRepository
from app.server.generation.binding import bind_generation_service
from app.server.generation.persistence.repository import GenerateTaskRepository
from app.server.generation.services import GenerationService
from app.server.infra.cache import app_cache
from app.server.infra.gateway import gateway_client
from app.server.infra.object_storage import object_storage
from app.server.ports.adapters import (
    AssetsPortAdapter,
    CanvasPortAdapter,
    ChatPortAdapter,
    GenerationPortAdapter,
    UpgradeInvitePortAdapter,
    UserSkillPortAdapter,
    WorkshopPortAdapter,
)
from app.server.ports.product import (
    AssetsPort,
    CanvasPort,
    ChatPort,
    GenerationPort,
    UpgradeInvitePort,
    UserSkillPort,
    WorkshopPort,
)
from app.server.skills.services import user_skill_service
from app.server.workshop.persistence.repository import WorkshopRepository
from app.server.workshop.services.task_orchestrator import WorkshopTaskOrchestrator
from app.server.workshop.services.expert_node_runner import ExpertNodeRunner
from app.server.workshop.services.workflow_run_execution import (
    WorkflowRunExecutionService,
)
from app.server.workshop.services.workflow_schedule_service import (
    WorkshopWorkflowScheduleService,
)
from app.server.workshop.services.workshop_project_service import WorkshopProjectService
from app.server.chat.services.selected_expert.service import ChatSelectedExpertService
from app.server.chat.services.upgrade_invite import UpgradeInviteService

chat_service = ChatService()
generation_service = GenerationService(
    task_repository=GenerateTaskRepository(),
    asset_repository=AssetRepository(),
    gateway_client=gateway_client,
    asset_service=asset_service,
    object_storage=object_storage,
    model_cache=app_cache,
)
bind_generation_service(generation_service)

generation_port: GenerationPort = GenerationPortAdapter(generation_service)
canvas_port: CanvasPort = CanvasPortAdapter()
chat_port: ChatPort = ChatPortAdapter(ChatAttachmentRepository())
assets_port: AssetsPort = AssetsPortAdapter(asset_service, AssetRepository())
user_skill_port: UserSkillPort = UserSkillPortAdapter(user_skill_service)

workshop_repository = WorkshopRepository()
workshop_project_service = WorkshopProjectService(workshop_repository)
chat_selected_expert_service = ChatSelectedExpertService(workshop_project_service)
upgrade_invite_service = UpgradeInviteService(workshop_project_service)
workshop_task_orchestrator = WorkshopTaskOrchestrator(workshop_repository)


def _enqueue_workflow_run(project_id: str, user_id: int, run_id: str) -> None:
    """将运行记录交给 Celery Worker"""
    from app.server.workshop.celery_app import enqueue_workflow_run

    enqueue_workflow_run(project_id, user_id, run_id)


def _revoke_workflow_run(project_id: str, user_id: int, run_id: str) -> None:
    """尽力撤销仍在队列中的 Celery 任务"""
    from app.server.workshop.celery_app import revoke_workflow_run

    revoke_workflow_run(project_id, user_id, run_id)


workshop_workflow_schedule_service = WorkshopWorkflowScheduleService(
    repository=workshop_repository,
    orchestrator=workshop_task_orchestrator,
    enqueue_run=_enqueue_workflow_run,
    revoke_run=_revoke_workflow_run,
)

workshop_run_execution_service = WorkflowRunExecutionService(
    repository=workshop_repository,
    runner=ExpertNodeRunner(
        projects=workshop_project_service,
        orchestrator=workshop_task_orchestrator,
        repository=workshop_repository,
    ),
)

workshop_port: WorkshopPort = WorkshopPortAdapter(
    workshop_project_service,
    workshop_workflow_schedule_service,
)
upgrade_invite_port: UpgradeInvitePort = UpgradeInvitePortAdapter(upgrade_invite_service)

configure_ports(
    generation=generation_port,
    canvas=canvas_port,
    chat=chat_port,
    assets=assets_port,
    user_skills=user_skill_port,
    workshop=workshop_port,
    upgrade_invite=upgrade_invite_port,
)

__all__ = [
    "assets_port",
    "canvas_port",
    "chat_port",
    "chat_service",
    "chat_selected_expert_service",
    "upgrade_invite_service",
    "upgrade_invite_port",
    "generation_port",
    "generation_service",
    "user_skill_port",
    "workshop_port",
    "workshop_project_service",
    "workshop_repository",
    "workshop_task_orchestrator",
    "workshop_workflow_schedule_service",
]
