from app.agent.chat.service import ChatService
from app.agent.runtime.ports import configure_ports
from app.server.assets.services.service import asset_service
from app.server.chat.services.attachments.service import chat_attachment_service
from app.server.generation.binding import bind_generation_service
from app.server.generation.services import GenerationService
from app.server.infra.cache import app_cache
from app.server.infra.gateway import gateway_client
from app.server.infra.object_storage import object_storage
from app.server.ports.adapters import (
    AssetsPortAdapter,
    CanvasPortAdapter,
    ChatPortAdapter,
    GenerationPortAdapter,
    UserSkillPortAdapter,
)
from app.server.ports.product import AssetsPort, CanvasPort, ChatPort, GenerationPort, UserSkillPort
from app.server.assets.persistence.repository import AssetRepository
from app.server.chat.persistence.attachment_repository import ChatAttachmentRepository
from app.server.generation.persistence.repository import GenerateTaskRepository
from app.server.skills.services import user_skill_service

chat_service = ChatService()
generation_service = GenerationService(
    task_repository=GenerateTaskRepository(),
    asset_repository=AssetRepository(),
    attachment_repository=ChatAttachmentRepository(),
    gateway_client=gateway_client,
    attachment_service=chat_attachment_service,
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

configure_ports(
    generation=generation_port,
    canvas=canvas_port,
    chat=chat_port,
    assets=assets_port,
    user_skills=user_skill_port,
)

__all__ = [
    "assets_port",
    "canvas_port",
    "chat_port",
    "chat_service",
    "generation_port",
    "generation_service",
    "user_skill_port",
]
