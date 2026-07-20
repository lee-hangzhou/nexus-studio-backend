from tortoise import fields
from tortoise.models import Model


class CanvasProjectMeta(Model):
    """画布项目元信息。

    revision 是画布补丁的乐观并发控制版本；节点/连线计数用于轻量上下文和列表展示。
    """

    project_id = fields.BigIntField(pk=True)
    revision = fields.BigIntField(null=False, default=0)
    node_count = fields.IntField(null=False, default=0)
    edge_count = fields.IntField(null=False, default=0)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "canvas_project_meta"
