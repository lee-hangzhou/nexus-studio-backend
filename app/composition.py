"""应用组合根：在此装配各层依赖，避免模块 import 时形成环。"""

from app.chat.service import ChatService

chat_service = ChatService()

__all__ = ["chat_service"]
