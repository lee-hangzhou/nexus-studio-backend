from app.agent.chat.service import ChatService
from app.agent.runtime.ports import configure_ports
from app.server.assets.services.service import asset_service
from app.server.chat.services.attachments.service import chat_attachment_service
from app.server.generation.services.generate_task import GenerateTaskService
from app.server.generation.services.generate_task_views import GenerateTaskViewAssembler
from app.server.infra.gateway import gateway_client
from app.server.ports.adapters import (
    AssetsPortAdapter,
    CanvasPortAdapter,
    ChatPortAdapter,
    GenerationPortAdapter,
)
from app.server.ports.product import AssetsPort, CanvasPort, ChatPort, GenerationPort
from app.server.assets.persistence.repository import AssetRepository
from app.server.chat.persistence.attachment_repository import ChatAttachmentRepository
from app.server.generation.persistence.repository import GenerateTaskRepository

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

generation_port: GenerationPort = GenerationPortAdapter(generate_task_service)
canvas_port: CanvasPort = CanvasPortAdapter()
chat_port: ChatPort = ChatPortAdapter(ChatAttachmentRepository())
assets_port: AssetsPort = AssetsPortAdapter(asset_service, AssetRepository())

configure_ports(
    generation=generation_port,
    canvas=canvas_port,
    chat=chat_port,
    assets=assets_port,
)

__all__ = [
    "assets_port",
    "canvas_port",
    "chat_port",
    "chat_service",
    "generate_task_service",
    "generation_port",
]
