from tortoise import fields
from tortoise.models import Model


class CanvasOperations(Model):
    """画布操作审计表

    每次 apply_patch 记录原始 payload, 供排查 Agent 行为
    """

    op_id = fields.UUIDField(pk=True)
    episode_id = fields.BigIntField(null=False)
    user_id = fields.BigIntField(null=False)
    turn_id = fields.CharField(max_length=64, null=True)
    op_type = fields.CharField(max_length=32, null=False)
    payload = fields.JSONField(null=False)
    status = fields.CharField(max_length=16, null=False)
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "canvas_operations"
