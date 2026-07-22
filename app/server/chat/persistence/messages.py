from tortoise import fields

from app.server.persistence.model_base import AppendOnlyModel


class ChatMessages(AppendOnlyModel):
    conversation_id = fields.BigIntField(null=False)
    user_id = fields.BigIntField(null=False)
    role = fields.IntField(null=False)
    content = fields.TextField(null=False)
    payload = fields.JSONField(null=False)
    metadata = fields.JSONField(null=False)

    class Meta(AppendOnlyModel.Meta):
        table = "chat_messages"
        abstract = False
        indexes = [("conversation_id", "created_at")]
