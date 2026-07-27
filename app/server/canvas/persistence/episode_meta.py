from tortoise import fields
from tortoise.models import Model


class CanvasEpisodeMeta(Model):
    episode_id = fields.BigIntField(pk=True)
    node_count = fields.IntField(null=False, default=0)
    edge_count = fields.IntField(null=False, default=0)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "canvas_episode_meta"
