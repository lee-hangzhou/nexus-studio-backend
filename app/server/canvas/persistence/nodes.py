from tortoise import fields
from tortoise.models import Model


class CanvasNodes(Model):
    """画布节点表。

    节点既是前端画布上的可视元素，也是工作流 runner 判断输入/输出状态的持久化单元。
    """

    id = fields.UUIDField(pk=True)
    episode_id = fields.BigIntField(null=False)
    kind = fields.CharField(max_length=16, null=False)
    revision = fields.BigIntField(null=False, default=1)
    position_x = fields.FloatField(null=False)
    position_y = fields.FloatField(null=False)
    title = fields.CharField(max_length=512, null=False, default="")
    input_prompt = fields.TextField(null=False, default="")
    output_text = fields.TextField(null=False, default="")
    status = fields.CharField(max_length=16, null=False, default="idle")
    model_id = fields.CharField(max_length=128, null=True)
    voice_id = fields.CharField(max_length=128, null=True)
    ratio = fields.CharField(max_length=16, null=True)
    duration_sec = fields.IntField(null=True)
    resolution = fields.CharField(max_length=16, null=True)
    task_id = fields.BigIntField(null=True)
    output_asset_ids = fields.JSONField(null=True)
    error_message = fields.TextField(null=True)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)
    deleted_at = fields.DatetimeField(null=True)

    class Meta:
        table = "canvas_nodes"
        indexes = [("episode_id", "status")]
