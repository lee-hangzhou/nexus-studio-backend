from tortoise import fields

from app.server.persistence.model_base import AppendOnlyModel


class CanvasMessages(AppendOnlyModel):
    """画布 Agent 会话消息表。

    使用 append-only 基类保留对话历史，metadata 存 turn_id、工具步骤摘要等辅助信息。
    """

    project_id = fields.BigIntField(null=False)
    user_id = fields.BigIntField(null=False)
    role = fields.SmallIntField(null=False)
    content = fields.TextField(null=False)
    metadata = fields.JSONField(null=False, default=dict)

    class Meta(AppendOnlyModel.Meta):
        table = "canvas_messages"
        abstract = False
        indexes = [("project_id", "created_at")]
