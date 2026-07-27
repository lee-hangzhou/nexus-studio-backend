from tortoise import fields

from app.server.persistence.model_base import BaseModel


class CanvasSessions(BaseModel):
    """画布 Agent 会话表, 仅创建者可见

    默认会话幂等依赖 schema.sql 部分唯一索引
    uk_canvas_sessions_default_alive (episode_id, user_id) WHERE status=1 AND is_default
    Tortoise 无法声明 partial unique, 以 SQL 为准
    """

    episode_id = fields.BigIntField(null=False)
    user_id = fields.BigIntField(null=False)
    title = fields.CharField(max_length=255, null=False)
    status = fields.SmallIntField(null=False)
    is_default = fields.BooleanField(null=False, default=False)

    class Meta(BaseModel.Meta):
        table = "canvas_sessions"
        abstract = False
        indexes = [
            ("episode_id", "user_id", "updated_at"),
            ("episode_id", "user_id", "status"),
        ]
