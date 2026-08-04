from tortoise import fields

from app.server.chat.domain.enums import ChatConversationKind
from app.server.persistence.model_base import BaseModel


class ChatConversations(BaseModel):
    user_id = fields.BigIntField(null=False)
    title = fields.CharField(max_length=255, null=False)
    default_model = fields.CharField(max_length=128, null=False)
    status = fields.IntField(null=False)
    kind = fields.CharField(
        max_length=32,
        null=False,
        default=ChatConversationKind.CHAT.value,
    )
    active_turn_id = fields.CharField(max_length=64, null=True)
    active_turn_started_at = fields.DatetimeField(null=True)
    selected_expert_key = fields.CharField(max_length=128, null=True)
    upgrade_invite_declined = fields.BooleanField(null=False, default=False)

    class Meta(BaseModel.Meta):
        table = "chat_conversations"
        abstract = False
        indexes = [("user_id", "updated_at")]
