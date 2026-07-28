from tortoise import fields
from tortoise.models import Model


class CanvasNodes(Model):
    """画布节点表

    envelope 列 + data JSONB 业务字段；节点既是可视元素也是工作流状态单元
    """

    id = fields.UUIDField(pk=True)
    episode_id = fields.BigIntField(null=False)
    kind = fields.CharField(max_length=16, null=False)
    revision = fields.BigIntField(null=False, default=1)
    position_x = fields.FloatField(null=False)
    position_y = fields.FloatField(null=False)
    width = fields.FloatField(null=True)
    height = fields.FloatField(null=True)
    data = fields.JSONField(null=False)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)
    deleted_at = fields.DatetimeField(null=True)

    class Meta:
        table = "canvas_nodes"
