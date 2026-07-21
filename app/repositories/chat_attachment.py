from collections.abc import Collection

from app.models.chat_attachments import ChatAttachments
from app.repositories.base import BaseRepository


class ChatAttachmentRepository(BaseRepository[ChatAttachments]):
    def __init__(self) -> None:
        self.model = ChatAttachments

    async def get_by_ids_for_user(
        self,
        attachment_ids: Collection[int],
        user_id: int,
    ) -> list[ChatAttachments]:
        if not attachment_ids:
            return []
        return await self.model.filter(
            id__in=attachment_ids,
            user_id=user_id,
        ).all()
