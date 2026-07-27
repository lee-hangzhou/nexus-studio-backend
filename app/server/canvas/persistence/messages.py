from tortoise import fields

from app.server.persistence.model_base import AppendOnlyModel


class CanvasMessages(AppendOnlyModel):
    """画布 Agent 会话消息表"""

    episode_id = fields.BigIntField(null=False)
    session_id = fields.BigIntField(null=False)
    user_id = fields.BigIntField(null=False)
    role = fields.SmallIntField(null=False)
    content = fields.TextField(null=False)
    metadata = fields.JSONField(null=False, default=dict)

    class Meta(AppendOnlyModel.Meta):
        table = "canvas_messages"
        abstract = False
        indexes = [("session_id", "created_at"), ("episode_id", "created_at")]
