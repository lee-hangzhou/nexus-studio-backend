from app.assets.service import asset_service
from app.chat.attachments.service import chat_attachment_service
from app.chat.service import ChatService
from app.core.gateway import gateway_client
from app.repositories.asset import AssetRepository
from app.repositories.chat_attachment import ChatAttachmentRepository
from app.repositories.generate_task import GenerateTaskRepository
from app.services.generate_task import GenerateTaskService
from app.services.generate_task_views import GenerateTaskViewAssembler

chat_service = ChatService()
generate_task_service = GenerateTaskService(
    task_repository=GenerateTaskRepository(),
    asset_repository=AssetRepository(),
    attachment_repository=ChatAttachmentRepository(),
    gateway_client=gateway_client,
    attachment_service=chat_attachment_service,
    view_assembler=GenerateTaskViewAssembler(
        asset_service=asset_service,
        attachment_service=chat_attachment_service,
    ),
)

__all__ = ["chat_service", "generate_task_service"]
