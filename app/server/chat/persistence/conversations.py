from tortoise import fields

from app.server.persistence.model_base import BaseModel


class ChatConversations(BaseModel):
    user_id = fields.BigIntField(null=False)
    title = fields.CharField(max_length=255, null=False)
    default_model = fields.CharField(max_length=128, null=False)
    status = fields.IntField(null=False)
    active_turn_id = fields.CharField(max_length=64, null=True)
    active_turn_started_at = fields.DatetimeField(null=True)

    class Meta(BaseModel.Meta):
        table = "chat_conversations"
        abstract = False
        indexes = [("user_id", "updated_at")]
