from tortoise import fields
from tortoise.models import Model


class CanvasEdges(Model):
    """画布连线表。

    连线不仅用于前端视觉连接，也表示数据依赖：source_port 输出到 target_port 输入。
    """

    id = fields.UUIDField(pk=True)
    project_id = fields.BigIntField(null=False)
    source_node_id = fields.UUIDField(null=False)
    target_node_id = fields.UUIDField(null=False)
    source_port = fields.CharField(max_length=64, null=False)
    target_port = fields.CharField(max_length=64, null=False)
    edge_type = fields.CharField(max_length=32, null=False, default="dependency")
    metadata = fields.JSONField(null=False, default=dict)
    created_at = fields.DatetimeField(auto_now_add=True)
    deleted_at = fields.DatetimeField(null=True)

    class Meta:
        table = "canvas_edges"
